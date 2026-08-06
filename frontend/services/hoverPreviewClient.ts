type HoverWorkerRequest =
  | { type: "start"; sessionId: number; canvas: OffscreenCanvas; url: string; name: string }
  | { type: "cancel"; sessionId: number };

type HoverWorkerResponse =
  | { type: "ready"; sessionId: number }
  | { type: "error"; sessionId: number; message: string };

let worker: Worker | null = null;
let workerInitFailed = false;
let nextSessionId = 1;

// Callbacks for the currently-active session only. A new session
// (startHoverPreview called again) replaces this before the old session's
// cancel is even sent, so a late/stale message from a just-superseded
// session can never invoke the wrong card's callbacks -- the sessionId
// check in the message handler below is the actual guard; this map exists
// so multiple rapid start/cancel pairs before a response arrives don't lose
// track of which callbacks belong to which sessionId.
const activeCallbacks = new Map<
  number,
  { onReady: () => void; onError: () => void }
>();

function getWorker(): Worker | null {
  if (workerInitFailed) return null;
  if (worker) return worker;
  if (typeof OffscreenCanvas === "undefined") {
    workerInitFailed = true;
    return null;
  }
  try {
    const w = new Worker(
      new URL("../workers/hoverPreviewWorker.ts", import.meta.url),
      { type: "module" },
    );
    w.onmessage = (event: MessageEvent<HoverWorkerResponse>) => {
      const msg = event.data;
      const callbacks = activeCallbacks.get(msg.sessionId);
      if (!callbacks) return; // superseded/cancelled session, ignore
      if (msg.type === "ready") {
        callbacks.onReady();
        // Deliberately NOT deleted here -- the session stays "active" after
        // ready so a LATER error (webglcontextlost, forwarded from the
        // worker's own contextLostHandler; or a worker-side crash mid-
        // animation) can still reach this session's onError. Without this,
        // "ready" always arrives first (synchronous, first-frame) and
        // unconditionally deleting the map entry here meant any later
        // error for the same session found the entry already gone and was
        // silently dropped at the `if (!callbacks) return;` line above --
        // defeating the whole point of hoverPreviewWorker.ts's
        // contextLostHandler, which exists specifically so a lost context
        // doesn't leave the canvas permanently blank with no error.
        // Cleanup happens via cancel() (which already deletes the entry
        // itself) or here, on an eventual error.
      } else {
        activeCallbacks.delete(msg.sessionId);
        console.warn(`[hoverPreviewClient] Hover preview failed: ${msg.message}`);
        callbacks.onError();
      }
    };
    w.onerror = () => {
      // A crashed worker can never be trusted again this session. Every
      // pending session's onError fires (matches this design's "no
      // synchronous fallback" rule -- a crash just means "no live preview
      // for the rest of this session," never a fallback render attempt),
      // and future startHoverPreview calls short-circuit via
      // workerInitFailed.
      console.warn(
        "[hoverPreviewClient] Worker crashed; live hover preview disabled for the rest of this session.",
      );
      for (const callbacks of activeCallbacks.values()) callbacks.onError();
      activeCallbacks.clear();
      w.terminate();
      workerInitFailed = true;
      worker = null;
    };
    w.onmessageerror = () => {
      console.warn(
        "[hoverPreviewClient] Worker sent an undeserializable message; live hover preview disabled for the rest of this session.",
      );
      for (const callbacks of activeCallbacks.values()) callbacks.onError();
      activeCallbacks.clear();
      w.terminate();
      workerInitFailed = true;
      worker = null;
    };
    worker = w;
    return worker;
  } catch (err) {
    console.warn(
      "[hoverPreviewClient] Failed to construct hover-preview worker; live hover preview disabled.",
      err,
    );
    workerInitFailed = true;
    return null;
  }
}

export function startHoverPreview(
  canvas: HTMLCanvasElement,
  model: { url: string; name: string },
  callbacks: { onReady: () => void; onError: () => void },
): { cancel: () => void } {
  const activeWorker = getWorker();
  if (!activeWorker) {
    // No synchronous fallback -- report failure immediately so the caller
    // falls back to the static thumbnail, per this design's explicit choice
    // to never reintroduce main-thread parsing for hover preview.
    callbacks.onError();
    return { cancel: () => {} };
  }

  const sessionId = nextSessionId++;
  activeCallbacks.set(sessionId, callbacks);

  let offscreen: OffscreenCanvas;
  try {
    offscreen = canvas.transferControlToOffscreen();
  } catch (err) {
    // A canvas can only have its control transferred once ever -- if this
    // element was somehow already transferred (shouldn't happen given
    // HoverPreviewCanvas always mounts a fresh <canvas>, but defended here
    // rather than assumed), fail this session the same way a worker-crash
    // would.
    activeCallbacks.delete(sessionId);
    console.warn(
      "[hoverPreviewClient] Failed to transfer canvas control to worker.",
      err,
    );
    callbacks.onError();
    return { cancel: () => {} };
  }

  const request: HoverWorkerRequest = {
    type: "start",
    sessionId,
    canvas: offscreen,
    url: model.url,
    name: model.name,
  };
  activeWorker.postMessage(request, [offscreen]);

  return {
    cancel: () => {
      activeCallbacks.delete(sessionId);
      const activeW = worker;
      if (activeW) {
        activeW.postMessage({ type: "cancel", sessionId } as HoverWorkerRequest);
      }
    },
  };
}
