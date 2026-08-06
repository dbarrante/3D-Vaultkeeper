// frontend/services/thumbnailGenerator.ts
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { loadStepGeometryFromBuffer } from "@/lib/stepGeometry";
import { buildThumbnailScene } from "@/lib/thumbnailScene";

export class ThumbnailTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ThumbnailTransportError";
  }
}

type WorkerRequest =
  | { reqId: number; kind: "url"; url: string; filename: string }
  | { reqId: number; kind: "blob"; blob: Blob; filename: string };

type WorkerResponse =
  | { reqId: number; ok: true; thumbnail: string }
  | { reqId: number; ok: false; kind: "transport" | "render"; message: string };

let worker: Worker | null = null;
let workerInitFailed = false;
let nextReqId = 1;
const pending = new Map<
  number,
  {
    resolve: (v: string) => void;
    reject: (e: Error) => void;
    timeoutId: ReturnType<typeof setTimeout>;
  }
>();

// A worker crash, an undeserializable message, or a request that never gets
// a response are all environment/timing failures -- not evidence that the
// underlying model file can't be rendered. They reject with
// ThumbnailTransportError (not a plain Error) so that callers such as
// App.tsx's background loop (which does `instanceof ThumbnailTransportError`
// to decide "retry later" vs. "permanently mark thumbnailFailed") treat them
// as transient, matching how a failed `fetch()` is already handled.
function failAllPending(message: string) {
  for (const entry of pending.values()) {
    clearTimeout(entry.timeoutId);
    entry.reject(new ThumbnailTransportError(message));
  }
  pending.clear();
}

function getWorker(): Worker | null {
  if (workerInitFailed) return null;
  if (worker) return worker;
  if (typeof OffscreenCanvas === "undefined") {
    workerInitFailed = true;
    return null;
  }
  try {
    const w = new Worker(
      new URL("../workers/thumbnailWorker.ts", import.meta.url),
      { type: "module" },
    );
    w.onmessage = (event: MessageEvent<WorkerResponse>) => {
      const msg = event.data;
      const entry = pending.get(msg.reqId);
      if (!entry) return;
      pending.delete(msg.reqId);
      clearTimeout(entry.timeoutId);
      // Comparing with `=== true` (rather than the more idiomatic `if
      // (msg.ok)`) is required for TypeScript to narrow the `ok: false`
      // branch's `kind`/`message` fields below -- this project's tsconfig
      // doesn't set strictNullChecks, and discriminated-union narrowing on
      // a boolean literal discriminant (as opposed to a string literal)
      // doesn't work reliably without it.
      if (msg.ok === true) {
        entry.resolve(msg.thumbnail);
      } else if (msg.kind === "transport") {
        entry.reject(new ThumbnailTransportError(msg.message));
      } else {
        entry.reject(new Error(msg.message));
      }
    };
    w.onerror = () => {
      // The worker crashed outright (not a per-request error message) --
      // fail every in-flight request and stop using this worker instance
      // for the rest of the session; future calls fall back to the
      // synchronous main-thread path below. Logged with console.warn (not
      // silent) since this is a session-wide degradation back to
      // main-thread-blocking rendering, and the dead worker is explicitly
      // terminated rather than left to be garbage collected.
      console.warn(
        "[thumbnailGenerator] Worker crashed; falling back to synchronous " +
          "main-thread thumbnail rendering for the rest of this session.",
      );
      failAllPending("Thumbnail worker crashed");
      w.terminate();
      workerInitFailed = true;
      worker = null;
    };
    w.onmessageerror = () => {
      // A message from the worker failed to deserialize -- the worker
      // connection can no longer be trusted, so treat it the same as an
      // outright crash: fail everything in-flight, terminate, and fall
      // back to the synchronous path for the rest of the session.
      console.warn(
        "[thumbnailGenerator] Worker sent an undeserializable message; " +
          "falling back to synchronous main-thread thumbnail rendering " +
          "for the rest of this session.",
      );
      failAllPending("Thumbnail worker message could not be deserialized");
      w.terminate();
      workerInitFailed = true;
      worker = null;
    };
    worker = w;
    return worker;
  } catch (err) {
    console.warn(
      "[thumbnailGenerator] Failed to construct thumbnail worker; falling " +
        "back to synchronous main-thread thumbnail rendering.",
      err,
    );
    workerInitFailed = true;
    return null;
  }
}

// Requests should never legitimately take anywhere near this long; this
// exists purely as a safety net so a hung or silently-killed worker (e.g. a
// silent OOM kill that fires neither `onerror` nor `onmessageerror`, or a
// worker-side fetch() that never resolves) can't wedge a caller's `await`
// forever. Callers such as App.tsx's background loop otherwise never reach
// their next scheduled tick and the UI's "Generating: <name>" status never
// clears for the rest of the session.
const REQUEST_TIMEOUT_MS = 120_000;

function requestFromWorker(
  activeWorker: Worker,
  request: WorkerRequest,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      if (pending.delete(request.reqId)) {
        reject(
          new ThumbnailTransportError(
            `Thumbnail worker request timed out after ${REQUEST_TIMEOUT_MS}ms`,
          ),
        );
      }
    }, REQUEST_TIMEOUT_MS);
    pending.set(request.reqId, { resolve, reject, timeoutId });
    try {
      activeWorker.postMessage(request);
    } catch (err) {
      // postMessage can throw synchronously (e.g. a structured-clone
      // failure, or a dead worker in some browser states) -- without this
      // catch, that throw would escape the Promise executor and reject
      // with the raw thrown value rather than a ThumbnailTransportError,
      // letting a transient/environment failure masquerade as a permanent
      // per-file failure the same way an unhandled `onerror` crash would.
      pending.delete(request.reqId);
      clearTimeout(timeoutId);
      reject(
        new ThumbnailTransportError(
          `Failed to post thumbnail request to worker: ${
            err instanceof Error ? err.message : String(err)
          }`,
        ),
      );
    }
  });
}

// --- Fallback path: used only when OffscreenCanvas/Worker construction is
// unsupported by the runtime. Mirrors the worker's rendering pipeline
// exactly, running synchronously on the main thread like this app did
// before the worker offload -- kept so the app degrades gracefully on an
// unsupported WebView2 runtime instead of breaking outright.

function renderObjectToDataUrlSync(object: THREE.Object3D): string {
  const { scene, camera } = buildThumbnailScene(object);

  const renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: true,
    preserveDrawingBuffer: true,
  });
  renderer.setSize(300, 300);
  renderer.render(scene, camera);

  const dataUrl = renderer.domElement.toDataURL("image/png");
  renderer.dispose();
  renderer.forceContextLoss();

  return dataUrl;
}

async function generateThumbnailFromArrayBufferSync(
  contents: ArrayBuffer,
  filename: string,
): Promise<string> {
  const lower = filename.toLowerCase();
  const is3MF = lower.endsWith(".3mf");
  const isSTL = lower.endsWith(".stl");
  const isSTP = lower.endsWith(".step") || lower.endsWith(".stp");

  if (!isSTL && !is3MF && !isSTP) {
    throw new Error("Unsupported file type for thumbnail");
  }

  let object: THREE.Object3D;

  if (is3MF) {
    const loader = new ThreeMFLoader();
    object = loader.parse(contents);
  } else if (isSTL) {
    const loader = new STLLoader();
    const geometry = loader.parse(contents);
    const material = new THREE.MeshStandardMaterial({
      color: 0x3b82f6,
      roughness: 0.5,
      metalness: 0.2,
    });
    object = new THREE.Mesh(geometry, material);
    object.rotation.y = 0.3;
  } else {
    object = await loadStepGeometryFromBuffer(contents);
    object.rotation.y = 90;
    object.rotation.z = -0.3;
  }

  const dataUrl = renderObjectToDataUrlSync(object);

  if (isSTL) {
    (object as THREE.Mesh).geometry.dispose();
    ((object as THREE.Mesh).material as THREE.Material).dispose();
  }

  return dataUrl;
}

// --- Public API -- signatures unchanged from before the worker offload, so
// every existing call site (App.tsx's background loop and upload flows,
// DetailPanel.tsx's file-replace flow) needs zero changes.

export const generateThumbnailFromArrayBuffer = async (
  contents: ArrayBuffer,
  filename: string,
): Promise<string> => {
  const activeWorker = getWorker();
  if (!activeWorker) {
    return generateThumbnailFromArrayBufferSync(contents, filename);
  }
  const reqId = nextReqId++;
  return requestFromWorker(activeWorker, {
    reqId,
    kind: "blob",
    blob: new Blob([contents]),
    filename,
  });
};

export const generateThumbnailFromUrl = async (
  url: string,
  filename: string,
): Promise<string> => {
  const activeWorker = getWorker();
  if (!activeWorker) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new ThumbnailTransportError(
        `Failed to fetch model file for thumbnail generation (${response.status} ${response.statusText}): ${url}`,
      );
    }
    const contents = await response.arrayBuffer();
    return generateThumbnailFromArrayBufferSync(contents, filename);
  }
  const reqId = nextReqId++;
  return requestFromWorker(activeWorker, { reqId, kind: "url", url, filename });
};

export const generateThumbnail = async (file: File): Promise<string> => {
  const activeWorker = getWorker();
  if (!activeWorker) {
    const contents = await file.arrayBuffer();
    return generateThumbnailFromArrayBufferSync(contents, file.name);
  }
  const reqId = nextReqId++;
  return requestFromWorker(activeWorker, {
    reqId,
    kind: "blob",
    blob: file,
    filename: file.name,
  });
};
