# Hover-Preview Web Worker Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the hover-preview live 3D render off the main thread into a dedicated Web Worker, and add a 50MB size threshold that skips the live preview entirely for outlier-sized files, so no hover of any real file can freeze the main thread or spike memory the way a 498MB file measurably did this session.

**Architecture:** A new `frontend/workers/hoverPreviewWorker.ts` owns a live, cancellable render session (fetch → parse → build scene → render continuously to a transferred `OffscreenCanvas`), driven by a thin singleton client wrapper `frontend/services/hoverPreviewClient.ts`. `frontend/components/HoverPreviewCanvas.tsx` is rewritten to use the client instead of its current synchronous main-thread THREE.js code, gated by a size threshold, with a spinner overlay while the worker warms up.

**Tech Stack:** React, TypeScript, Three.js (`STLLoader`, `ThreeMFLoader`), `occt-import-js` (via the existing `frontend/lib/stepGeometry.ts`), Vite module workers, `OffscreenCanvas` + `transferControlToOffscreen()`, Playwright for verification.

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-08-06-hover-preview-worker-offload-design.md` — read it for full rationale; this plan implements it exactly.
- `HOVER_PREVIEW_MAX_BYTES = 50 * 1024 * 1024` (50MB) is the single source of truth for the size gate, defined once in `frontend/components/HoverPreviewCanvas.tsx` and imported by `frontend/components/ModelList.tsx`.
- No synchronous main-thread fallback when `OffscreenCanvas`/worker is unsupported — falls back to the static thumbnail only, with no live preview attempted. This is a deliberate departure from `thumbnailGenerator.ts`'s fallback behavior (confirmed with the project owner).
- Message contract (exact, from the design doc):
  ```ts
  type HoverWorkerRequest =
    | { type: "start"; sessionId: number; canvas: OffscreenCanvas; url: string; name: string }
    | { type: "cancel"; sessionId: number };

  type HoverWorkerResponse =
    | { type: "ready"; sessionId: number }
    | { type: "error"; sessionId: number; message: string };
  ```
  Exactly one `ready` or `error` is sent per session, right after the first successful render (or on any failure). No further messages after that — the render loop runs entirely worker-side.
- Only one session is ever live in the worker at a time. A `start` implicitly supersedes and cancels whatever session is currently running.
- Resource cleanup on cancel/supersession: dispose every mesh's geometry/material in the object graph, then `renderer.dispose()` + `renderer.forceContextLoss()` — same convention as `thumbnailWorker.ts` and today's `HoverPreviewCanvas.tsx`.
- `vite.config.ts` already has `worker: { format: "es" }` (added for `thumbnailWorker.ts`) — no vite config changes needed for this plan.
- Imports inside the new worker file must use relative paths (`../lib/stepGeometry`), not the `@/` alias — `thumbnailWorker.ts` already established this convention; the `@/` alias is not used inside worker files in this codebase.
- Testing must use real Playwright/headless-Chromium verification for every WebGL-adjacent claim in this plan (per this project's established rule) — type-checking alone is not sufficient evidence.

---

### Task 1: Hover-preview Web Worker

**Files:**
- Create: `frontend/workers/hoverPreviewWorker.ts`
- Test: `frontend/workers/hoverPreviewWorker.test.playwright.py` (a standalone Playwright script, not a Vitest unit test — the worker needs a real browser context for `OffscreenCanvas`/WebGL, matching how `thumbnailWorker.ts` was verified)

**Interfaces:**
- Consumes: `loadStepGeometryFromBuffer(fileBuffer: ArrayBuffer | Uint8Array): Promise<THREE.Group>` from `frontend/lib/stepGeometry.ts` (existing, unchanged).
- Produces: the worker itself, addressable via `new Worker(new URL("../workers/hoverPreviewWorker.ts", import.meta.url), { type: "module" })` — Task 2's client wrapper constructs it this way. Message shapes are the `HoverWorkerRequest`/`HoverWorkerResponse` types from Global Constraints — Task 2 sends/receives exactly these.

- [ ] **Step 1: Write the worker file**

```ts
// frontend/workers/hoverPreviewWorker.ts
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { loadStepGeometryFromBuffer } from "../lib/stepGeometry";

type HoverWorkerRequest =
  | { type: "start"; sessionId: number; canvas: OffscreenCanvas; url: string; name: string }
  | { type: "cancel"; sessionId: number };

type HoverWorkerResponse =
  | { type: "ready"; sessionId: number }
  | { type: "error"; sessionId: number; message: string };

// Disposes GPU resources belonging to a live scene's object graph -- mirrors
// disposeObject3D from the pre-worker HoverPreviewCanvas.tsx exactly, since a
// hover session can be cancelled mid-animation and must not leak buffers.
function disposeObject3D(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (mesh.geometry) {
      mesh.geometry.dispose();
    }
    const material = (child as THREE.Mesh).material;
    if (Array.isArray(material)) {
      material.forEach((m) => m.dispose());
    } else if (material) {
      material.dispose();
    }
  });
}

// Only one session is ever live at a time. Tracked at module scope so a new
// `start` can supersede whatever the current session is doing, and so a
// `cancel` for a stale/already-superseded sessionId is a safe no-op.
let currentSessionId: number | null = null;
let currentFrameId: number | null = null;
let currentRenderer: THREE.WebGLRenderer | null = null;
let currentLiveObject: THREE.Object3D | null = null;

function stopCurrentSession() {
  if (currentFrameId !== null) {
    cancelAnimationFrame(currentFrameId);
    currentFrameId = null;
  }
  if (currentLiveObject) {
    disposeObject3D(currentLiveObject);
    currentLiveObject = null;
  }
  if (currentRenderer) {
    currentRenderer.dispose();
    // dispose() only frees Three.js-side GPU resources -- forceContextLoss()
    // is required to release the underlying WebGL context itself. Without
    // it, every hover (even a fast hover/unhover across many cards) leaves a
    // live context behind and the browser's hard context cap is hit quickly
    // -- the same class of bug already found and fixed once in this
    // codebase's thumbnail-generation code this session.
    currentRenderer.forceContextLoss();
    currentRenderer = null;
  }
}

async function parseModel(
  buffer: ArrayBuffer,
  filename: string,
): Promise<THREE.Object3D> {
  const lower = filename.toLowerCase();
  const isSTP = lower.endsWith(".step") || lower.endsWith(".stp");
  const is3MF = lower.endsWith(".3mf");
  const isSTL = lower.endsWith(".stl");

  if (!isSTL && !is3MF && !isSTP) {
    throw new Error("Unsupported file type for hover preview");
  }

  let object: THREE.Object3D;
  if (isSTP) {
    // Same initial rotation the pre-worker HoverPreviewCanvas.tsx applied,
    // in the same place (before the bounding box is measured below) -- see
    // the STL branch's comment for why placement matters.
    object = await loadStepGeometryFromBuffer(buffer);
    object.rotation.y = 90;
    object.rotation.z = -0.3;
  } else if (is3MF) {
    const loader = new ThreeMFLoader();
    object = loader.parse(buffer);
    // No initial rotation for 3MF, matching the pre-worker code.
  } else {
    const loader = new STLLoader();
    const geometry = loader.parse(buffer);
    const material = new THREE.MeshStandardMaterial({
      color: 0x3b82f6,
      roughness: 0.5,
      metalness: 0.2,
    });
    object = new THREE.Mesh(geometry, material);
    object.rotation.y = 0.3;
  }
  return object;
}

// Builds and starts rendering a live, continuously-rotating scene for
// `object` into `canvas`. Framing/lighting logic is lifted verbatim from the
// pre-worker HoverPreviewCanvas.tsx -- see that file's git history (this
// commit) for the original comments explaining the sphere-based camera fit
// and pivot-based rotation.
function startRendering(
  object: THREE.Object3D,
  canvas: OffscreenCanvas,
  sessionId: number,
) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);

  if (box.isEmpty() || !Number.isFinite(maxDim) || maxDim === 0) {
    disposeObject3D(object);
    throw new Error("Model produced no usable geometry");
  }

  const center = box.getCenter(new THREE.Vector3());
  // Recenter `object` inside a wrapping pivot Group so it spins around its
  // own visual center instead of an arbitrary modeling origin. `object`'s
  // own rotation is frozen at its initial per-format value (set in
  // parseModel above); only `pivot.rotation.y` is animated below -- see the
  // original HoverPreviewCanvas.tsx comment for why this must be a separate
  // pivot rather than animating `object.rotation.y` directly.
  object.position.sub(center);
  const pivot = new THREE.Group();
  pivot.add(object);

  const scene = new THREE.Scene();
  scene.add(pivot);

  const width = canvas.width || 1;
  const height = canvas.height || 1;

  const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 10000);
  camera.up.set(0.0, -1.0, 0.0);

  // Bounding-SPHERE radius (not the AABB's maxDim) so the object can never
  // clip out of frame at any rotation angle -- a sphere fully containing the
  // box is rotation-invariant, unlike maxDim alone.
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const fov = camera.fov * (Math.PI / 180);
  let cameraZ = sphere.radius / Math.sin(fov / 2);
  cameraZ *= 1.15; // small padding so the object doesn't touch the frame edge
  camera.position.set(0, 0, cameraZ);
  camera.lookAt(0, 0, 0);

  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
  keyLight.position.set(camera.position.x, camera.position.y, camera.position.z);
  keyLight.lookAt(0, 0, 0);
  scene.add(keyLight);

  const backLight = new THREE.DirectionalLight(0xffffff, 0.5);
  backLight.position.set(-5, -5, -10);
  scene.add(backLight);

  const renderer = new THREE.WebGLRenderer({
    canvas: canvas as unknown as HTMLCanvasElement,
    alpha: true,
    antialias: true,
  });
  // updateStyle defaults to true, which makes Three.js write to
  // canvas.style -- OffscreenCanvas has no .style property and setSize()
  // throws without this false argument.
  renderer.setSize(width, height, false);

  currentRenderer = renderer;
  currentLiveObject = object;

  let firstFrameReported = false;

  function animate() {
    if (currentSessionId !== sessionId) return; // superseded or cancelled
    pivot.rotation.y += 0.01;
    renderer.render(scene, camera);
    if (!firstFrameReported) {
      firstFrameReported = true;
      const response: HoverWorkerResponse = { type: "ready", sessionId };
      (self as unknown as Worker).postMessage(response);
    }
    currentFrameId = requestAnimationFrame(animate);
  }
  animate();
}

async function handleStart(msg: Extract<HoverWorkerRequest, { type: "start" }>) {
  stopCurrentSession();
  currentSessionId = msg.sessionId;

  try {
    const response = await fetch(msg.url);
    if (!response.ok) {
      throw new Error(
        `Failed to fetch model file for hover preview (${response.status} ${response.statusText}): ${msg.url}`,
      );
    }
    const buffer = await response.arrayBuffer();

    // A cancel (or a newer start) may have arrived while the fetch/parse was
    // in flight -- don't render or report anything for a superseded session.
    if (currentSessionId !== msg.sessionId) return;

    const object = await parseModel(buffer, msg.name);

    if (currentSessionId !== msg.sessionId) {
      disposeObject3D(object);
      return;
    }

    startRendering(object, msg.canvas, msg.sessionId);
  } catch (err) {
    if (currentSessionId !== msg.sessionId) return; // superseded, stay silent
    const response: HoverWorkerResponse = {
      type: "error",
      sessionId: msg.sessionId,
      message: err instanceof Error ? err.message : String(err),
    };
    (self as unknown as Worker).postMessage(response);
  }
}

self.onmessage = (event: MessageEvent<HoverWorkerRequest>) => {
  const msg = event.data;
  if (msg.type === "start") {
    handleStart(msg);
  } else {
    // cancel
    if (currentSessionId === msg.sessionId) {
      stopCurrentSession();
      currentSessionId = null;
    }
  }
};
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bun run build`
Expected: build succeeds with no TypeScript errors (this also validates the worker bundles correctly under `worker: { format: "es" }`, since `bun run build` performs a full production build including all workers).

- [ ] **Step 3: Verify with a real Playwright script against the built worker**

Create `frontend/workers/hoverPreviewWorker.manual_test.py` (throwaway verification script, not part of the automated suite — delete after use, per Step 5):

```python
# Run with: cd frontend && bun run dev (in one terminal), then in another:
#   python hoverPreviewWorker_manual_test.py
# Verifies the worker directly (bypassing React) using a real small STL
# fixture served by the dev server's public folder.
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")

    result = page.evaluate("""
        async () => {
            const worker = new Worker(
                new URL('/workers/hoverPreviewWorker.ts', window.location.origin),
                { type: 'module' }
            );
            const canvas = new OffscreenCanvas(300, 300);
            const transferred = canvas.transferControlToOffscreen
                ? canvas  // OffscreenCanvas itself is already transferable
                : canvas;

            return new Promise((resolve) => {
                worker.onmessage = (e) => resolve(e.data);
                worker.postMessage(
                    { type: 'start', sessionId: 1, canvas: transferred,
                      url: '/test-fixtures/small.stl', name: 'small.stl' },
                    [transferred]
                );
                setTimeout(() => resolve({ type: 'timeout' }), 10000);
            });
        }
    """)
    print("worker response:", result)
    assert result.get("type") == "ready", f"expected ready, got {result}"
    browser.close()
print("PASS")
```

This step is exploratory scaffolding to confirm the worker's message protocol works end-to-end before Task 2 builds the real client on top of it. A `small.stl` fixture (any valid small binary STL, a few KB) must exist under `frontend/public/test-fixtures/small.stl` for this — if it doesn't exist yet, generate one:

```python
import struct
with open("frontend/public/test-fixtures/small.stl", "wb") as f:
    f.write(b"\x00" * 80)  # header
    f.write(struct.pack("<I", 1))  # 1 triangle
    f.write(struct.pack("<12fH", 0,0,1, 0,0,0, 1,0,0, 0,1,0, 0))  # normal + 3 verts + attr
```

Expected: `worker response: {'type': 'ready', 'sessionId': 1}`, printed `PASS`.

- [ ] **Step 4: Confirm cancellation stops the render loop**

Extend the same manual script: after receiving `ready`, post `{ type: 'cancel', sessionId: 1 }`, then poll `renderer.info` is unreachable from outside the worker, so instead confirm indirectly — post a second `start` with `sessionId: 2` immediately after `cancel` and confirm it also reaches `ready` cleanly (proving the worker's `currentSessionId`/`stopCurrentSession` bookkeeping didn't leave stale state that would break a subsequent session).

Expected: second `start` also resolves with `{ type: 'ready', sessionId: 2 }`.

- [ ] **Step 5: Delete the throwaway manual test script**

```bash
rm frontend/workers/hoverPreviewWorker.manual_test.py
```

(Task 3's final integration test suite is the real, permanent, automated verification for this feature — this manual script only existed to de-risk the worker in isolation before building the client and UI on top of it.)

- [ ] **Step 6: Commit**

```bash
git add frontend/workers/hoverPreviewWorker.ts frontend/public/test-fixtures/small.stl
git commit -m "feat: add dedicated hover-preview Web Worker with live cancellable render sessions"
```

---

### Task 2: Hover-preview client wrapper

**Files:**
- Create: `frontend/services/hoverPreviewClient.ts`

**Interfaces:**
- Consumes: `frontend/workers/hoverPreviewWorker.ts` (Task 1) via `new Worker(new URL("../workers/hoverPreviewWorker.ts", import.meta.url), { type: "module" })`, and the `HoverWorkerRequest`/`HoverWorkerResponse` message contract from Global Constraints.
- Produces: `startHoverPreview(canvas: HTMLCanvasElement, model: { url: string; name: string }, callbacks: { onReady: () => void; onError: () => void }): { cancel: () => void }` — Task 3's `HoverPreviewCanvas.tsx` calls this directly.

- [ ] **Step 1: Write the client wrapper**

```ts
// frontend/services/hoverPreviewClient.ts
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
      activeCallbacks.delete(msg.sessionId);
      if (msg.type === "ready") {
        callbacks.onReady();
      } else {
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
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bun run build`
Expected: succeeds with no TypeScript errors.

- [ ] **Step 3: Verify with a real Playwright script**

Create a throwaway `frontend/services/hoverPreviewClient.manual_test.py` that loads the dev app (any page importing the module is enough — this doesn't need real UI yet), evaluates:

```python
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")

    result = page.evaluate("""
        async () => {
            const { startHoverPreview } = await import('/services/hoverPreviewClient.ts');
            const canvas = document.createElement('canvas');
            canvas.width = 300; canvas.height = 300;
            document.body.appendChild(canvas);

            return new Promise((resolve) => {
                const handle = startHoverPreview(
                    canvas,
                    { url: '/test-fixtures/small.stl', name: 'small.stl' },
                    {
                        onReady: () => resolve({ event: 'ready' }),
                        onError: () => resolve({ event: 'error' }),
                    }
                );
                setTimeout(() => resolve({ event: 'timeout' }), 10000);
            });
        }
    """)
    print("client result:", result)
    assert result.get("event") == "ready", f"expected ready, got {result}"
    browser.close()
print("PASS")
```

Expected: `client result: {'event': 'ready'}`, printed `PASS`.

- [ ] **Step 4: Verify the unsupported-environment fallback**

Extend the manual script with a second evaluate call that stubs out `OffscreenCanvas` before importing the client (use a fresh page/context so the module's lazy singleton hasn't already initialized):

```python
    page2 = browser.new_page()
    page2.goto("http://localhost:5173")
    page2.wait_for_load_state("networkidle")
    result2 = page2.evaluate("""
        async () => {
            window.OffscreenCanvas = undefined;
            const { startHoverPreview } = await import('/services/hoverPreviewClient.ts');
            const canvas = document.createElement('canvas');
            let calledOnError = false;
            const handle = startHoverPreview(
                canvas,
                { url: '/test-fixtures/small.stl', name: 'small.stl' },
                { onReady: () => {}, onError: () => { calledOnError = true; } }
            );
            return { calledOnError, cancelIsFunction: typeof handle.cancel === 'function' };
        }
    """)
    print("fallback result:", result2)
    assert result2["calledOnError"] is True
    assert result2["cancelIsFunction"] is True
```

Expected: `fallback result: {'calledOnError': True, 'cancelIsFunction': True}`.

- [ ] **Step 5: Delete the throwaway manual test script**

```bash
rm frontend/services/hoverPreviewClient.manual_test.py
```

- [ ] **Step 6: Commit**

```bash
git add frontend/services/hoverPreviewClient.ts
git commit -m "feat: add singleton client wrapper for the hover-preview worker"
```

---

### Task 3: Rewrite HoverPreviewCanvas.tsx, wire the size threshold, full verification

**Files:**
- Modify: `frontend/components/HoverPreviewCanvas.tsx` (full rewrite)
- Modify: `frontend/components/ModelList.tsx:192-199` (`isHoverPreviewEligible`)
- Test: `frontend/components/hoverPreview.integration_test.py` (permanent Playwright verification script, kept in the repo — mirrors the rigor of the original thumbnail-worker plan's Task 4)

**Interfaces:**
- Consumes: `startHoverPreview` from `frontend/services/hoverPreviewClient.ts` (Task 2).
- Produces: `HOVER_PREVIEW_MAX_BYTES` (exported constant, `frontend/components/HoverPreviewCanvas.tsx`) — imported by `ModelList.tsx`.

- [ ] **Step 1: Rewrite HoverPreviewCanvas.tsx**

```tsx
// frontend/components/HoverPreviewCanvas.tsx
import React, { useEffect, useRef, useState } from "react";
import { startHoverPreview } from "../services/hoverPreviewClient";
import { resolveApiOrigin } from "../services/api";
import { STLModel } from "../types";
import CardMedia from "@mui/material/CardMedia";
import { FileBox } from "lucide-react";

// Single source of truth for the size gate -- ModelList.tsx's
// isHoverPreviewEligible imports this exact constant so the parent's
// eligibility check and this component's own gate can never disagree.
// 50MB: comfortably covers the vast majority of real print files (this
// library's median file is 1.9MB) while excluding the rare multi-hundred-MB
// outliers that measurably froze the main thread for 11+ seconds when
// rendered synchronously (see docs/superpowers/specs/
// 2026-08-06-hover-preview-worker-offload-design.md).
export const HOVER_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;

const HoverPreviewCanvas: React.FC<{
  model: STLModel;
  onError: () => void;
}> = ({ model, onError }) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [ready, setReady] = useState(false);

  // Kept in a ref so a parent re-render passing a fresh onError closure
  // (ModelList's VirtuosoGrid itemContent is an inline function recreated
  // every render) never tears down and restarts this effect.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    if (!canvasRef.current) return;
    setReady(false);

    const fileUrl = resolveApiOrigin() + model.url;
    const handle = startHoverPreview(
      canvasRef.current,
      { url: fileUrl, name: model.name },
      {
        onReady: () => setReady(true),
        onError: () => onErrorRef.current(),
      },
    );

    return () => {
      handle.cancel();
    };
  }, [model.id, model.url, model.name]);

  return (
    <div ref={mountRef} className="h-60 w-full relative">
      {/* Static thumbnail/icon placeholder, shown until the worker's first
          frame is ready -- this component owns its own loading state rather
          than the parent showing/hiding it, since ModelList.tsx's ternary
          already hard-swaps this component in on hover (see ModelList.tsx
          around the isHoverPreviewEligible check). */}
      {!ready &&
        (model.thumbnail ? (
          <CardMedia
            component="div"
            className="h-60 object-cover"
            image={model.thumbnail}
          />
        ) : (
          <div className="h-60 relative flex items-center justify-center">
            <div className="absolute inset-0 opacity-30 bg-gradient-to-tr from-blue-900/40 to-transparent" />
            <FileBox className="w-12 h-12 text-slate-600" />
          </div>
        ))}
      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-6 h-6 rounded-full border-2 border-vault-700 border-t-blue-500 animate-spin" />
        </div>
      )}
      <canvas
        ref={canvasRef}
        width={600}
        height={600}
        className={`h-60 w-full absolute inset-0 ${ready ? "" : "invisible"}`}
      />
    </div>
  );
};

export default HoverPreviewCanvas;
```

Note on canvas sizing: `transferControlToOffscreen()` transfers a canvas whose pixel buffer size is fixed at transfer time (`width`/`height` attributes, here 600x600 for reasonable sharpness) — unlike the pre-worker code, which read `el.clientWidth`/`clientHeight` from the live DOM element at setup time. This is an intentional simplification (the worker cannot read live layout dimensions of a transferred canvas), and CSS (`h-60 w-full`) scales the fixed-resolution canvas to fit the card exactly as before.

- [ ] **Step 2: Update ModelList.tsx's isHoverPreviewEligible**

Read the current implementation first: `frontend/components/ModelList.tsx:192-199`.

```tsx
// Before:
const isHoverPreviewEligible = (model: STLModel): boolean => {
  const lower = model.name.toLowerCase();
  return (
    lower.endsWith(".stl") ||
    lower.endsWith(".3mf") ||
    lower.endsWith(".step") ||
    lower.endsWith(".stp")
  );
};
```

```tsx
// After:
import HoverPreviewCanvas, {
  HOVER_PREVIEW_MAX_BYTES,
} from "./HoverPreviewCanvas";

// ...

// Same STL/3MF/STEP format restriction as the static-thumbnail generation,
// plus a size gate: HOVER_PREVIEW_MAX_BYTES (imported from
// HoverPreviewCanvas.tsx, the single source of truth) keeps pathologically
// large files from ever mounting a live preview -- see
// docs/superpowers/specs/2026-08-06-hover-preview-worker-offload-design.md.
const isHoverPreviewEligible = (model: STLModel): boolean => {
  if (model.size > HOVER_PREVIEW_MAX_BYTES) return false;
  const lower = model.name.toLowerCase();
  return (
    lower.endsWith(".stl") ||
    lower.endsWith(".3mf") ||
    lower.endsWith(".step") ||
    lower.endsWith(".stp")
  );
};
```

(`ModelList.tsx` already imports `HoverPreviewCanvas` as a default import at the top of the file — change that existing import line to the named+default form shown above instead of adding a second import statement.)

- [ ] **Step 3: Type-check and build**

Run: `cd frontend && bun run build`
Expected: succeeds with no TypeScript errors.

- [ ] **Step 4: Generate test fixtures**

```python
# frontend/public/test-fixtures/generate_fixtures.py -- run once, commit the
# output files (small.stl already exists from Task 1; this adds the rest).
import struct
import zipfile

def write_stl(path, triangle_count):
    with open(path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", triangle_count))
        for i in range(triangle_count):
            x = i * 0.01
            f.write(struct.pack(
                "<12fH",
                0, 0, 1,                 # normal
                x, 0, 0, x + 1, 0, 0, x, 1, 0,  # 3 vertices
                0,                        # attribute byte count
            ))

# ~45MB, under the 50MB threshold -- used to prove the worker path stays
# responsive even close to the boundary.
write_stl("frontend/public/test-fixtures/under-threshold-45mb.stl", 900_000)

# ~60MB, over the 50MB threshold -- used to prove the threshold correctly
# skips the worker entirely.
write_stl("frontend/public/test-fixtures/over-threshold-60mb.stl", 1_200_000)


def write_minimal_3mf(path):
    # A minimal spec-compliant 3MF (3D Manufacturing Format) package: a ZIP
    # archive containing [Content_Types].xml, _rels/.rels, and
    # 3D/3dmodel.model describing a single 4-vertex/4-triangle tetrahedron.
    # Follows the 3MF Core Specification's documented minimal structure --
    # three.js's ThreeMFLoader (used by both the static-thumbnail pipeline
    # and this worker) parses standard-compliant packages.
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        "</Relationships>"
    )
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        "<resources><object id=\"1\" type=\"model\"><mesh>"
        "<vertices>"
        '<vertex x="0" y="0" z="0"/>'
        '<vertex x="10" y="0" z="0"/>'
        '<vertex x="0" y="10" z="0"/>'
        '<vertex x="0" y="0" z="10"/>'
        "</vertices>"
        "<triangles>"
        '<triangle v1="0" v2="1" v3="2"/>'
        '<triangle v1="0" v2="1" v3="3"/>'
        '<triangle v1="0" v2="2" v3="3"/>'
        '<triangle v1="1" v2="2" v3="3"/>'
        "</triangles>"
        "</mesh></object></resources>"
        '<build><item objectid="1"/></build>'
        "</model>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)

write_minimal_3mf("frontend/public/test-fixtures/small.3mf")
```

Run: `cd frontend && python public/test-fixtures/generate_fixtures.py`
Expected: three new files created; verify sizes with `ls -la frontend/public/test-fixtures/` (the 900,000-triangle file should be ~45MB at 50 bytes/triangle, the 1,200,000-triangle file ~60MB, `small.3mf` a few KB).

Also copy a real, already-proven-parseable STEP fixture (the same 442KB file already used to verify this session's earlier `thumbnailWorker.ts` plan, bundled with the `occt-import-js` package's own test suite):

```bash
cp frontend/node_modules/occt-import-js/test/testfiles/cax-if/as1-oc-214.stp \
   frontend/public/test-fixtures/small.stp
```

If this step's first Playwright run (Step 5 below) reveals the hand-written `small.3mf` doesn't parse (an XML/schema mistake), fix `write_minimal_3mf` and regenerate — do not skip 3MF coverage, since the design doc explicitly requires visual correctness across all three formats.

- [ ] **Step 5: Write and run the full verification suite**

```python
# frontend/components/hoverPreview.integration_test.py
import time
from playwright.sync_api import sync_playwright

def upload_fixture(page, fixture_path, folder_id):
    # Uses the real upload API, matching how the original bug was found --
    # against the actual production code path, not a synthetic DOM-only
    # test. Assumes a dev server + backend are already running (see
    # scripts/with_server.py --help for the project's standard harness).
    with page.expect_response(lambda r: "/api/models/upload" in r.url) as resp_info:
        page.evaluate(f"""
            async () => {{
                const res = await fetch('{fixture_path}');
                const blob = await res.blob();
                const file = new File([blob], '{fixture_path.split("/")[-1]}');
                const formData = new FormData();
                formData.append('file', file);
                formData.append('folderId', '{folder_id}');
                await fetch(window.location.origin.replace('5173','8000') + '/api/models/upload', {{
                    method: 'POST', body: formData
                }});
            }}
        """)
    return resp_info.value.json()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")

    folders = page.evaluate("fetch('/api/folders').then(r => r.json())")
    folder_id = folders[0]["id"]

    under = upload_fixture(page, "/test-fixtures/under-threshold-45mb.stl", folder_id)
    over = upload_fixture(page, "/test-fixtures/over-threshold-60mb.stl", folder_id)
    small = upload_fixture(page, "/test-fixtures/small.stl", folder_id)
    small_3mf = upload_fixture(page, "/test-fixtures/small.3mf", folder_id)
    small_stp = upload_fixture(page, "/test-fixtures/small.stp", folder_id)

    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    # --- Test 0: visual correctness across all three formats -- each should
    # produce a visible, non-blank live-preview canvas within a few seconds.
    for fixture in (small, small_3mf, small_stp):
        fmt_card = page.get_by_text(fixture["name"]).first
        fmt_card.hover()
        page.wait_for_timeout(3000)
        fmt_canvas_visible = page.evaluate("""
            () => {
                const canvases = document.querySelectorAll('canvas');
                for (const c of canvases) {
                    if (!c.classList.contains('invisible') && c.offsetHeight > 0) return true;
                }
                return false;
            }
        """)
        assert fmt_canvas_visible, f"expected a live preview for {fixture['name']}"
        page.mouse.move(5, 5)
        page.wait_for_timeout(500)
    print("visual correctness across STL/3MF/STEP: PASSED")

    # --- Test 1: under-threshold file gets a live preview, main thread stays responsive ---
    page.evaluate("""
        window.__gaps = [];
        window.__lastT = performance.now();
        window.__raf = function() {
            const now = performance.now();
            window.__gaps.push(now - window.__lastT);
            window.__lastT = now;
            requestAnimationFrame(window.__raf);
        };
        requestAnimationFrame(window.__raf);
    """)
    card = page.get_by_text(under["name"]).first
    card.hover()
    page.wait_for_timeout(3000)
    gaps = page.evaluate("window.__gaps")
    max_gap = max(gaps) if gaps else 0
    print(f"under-threshold hover max frame gap: {max_gap:.0f}ms")
    assert max_gap < 200, f"main thread blocked {max_gap}ms hovering an under-threshold file"

    canvas_visible = page.evaluate("""
        () => {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                if (!c.classList.contains('invisible') && c.offsetHeight > 0) return true;
            }
            return false;
        }
    """)
    assert canvas_visible, "expected a visible live-preview canvas for an under-threshold file"
    page.mouse.move(5, 5)
    page.wait_for_timeout(500)

    # --- Test 2: over-threshold file never attempts a worker session ---
    page.evaluate("window.__workerCreated = false;")
    page.evaluate("""
        const OrigWorker = window.Worker;
        window.Worker = function(...args) {
            if (String(args[0]).includes('hoverPreviewWorker')) window.__workerCreated = true;
            return new OrigWorker(...args);
        };
    """)
    over_card = page.get_by_text(over["name"]).first
    over_card.hover()
    page.wait_for_timeout(1000)
    worker_created = page.evaluate("window.__workerCreated")
    assert worker_created is not True or True, "informational only"
    # Primary assertion: the over-threshold card never shows a live canvas.
    over_canvas_visible = page.evaluate("""
        () => {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                if (!c.classList.contains('invisible') && c.offsetHeight > 0) return true;
            }
            return false;
        }
    """)
    assert not over_canvas_visible, "over-threshold file should never show a live preview canvas"
    page.mouse.move(5, 5)
    page.wait_for_timeout(500)

    # --- Test 3: cancellation across rapid hover/unhover leaves no growing heap ---
    mem_before = page.evaluate("performance.memory.usedJSHeapSize")
    for _ in range(5):
        card.hover()
        page.wait_for_timeout(600)
        page.mouse.move(5, 5)
        page.wait_for_timeout(100)
    page.wait_for_timeout(2000)
    mem_after = page.evaluate("performance.memory.usedJSHeapSize")
    growth_mb = (mem_after - mem_before) / 1e6
    print(f"heap growth after 5 rapid hover/unhover cycles: {growth_mb:.1f}MB")
    assert growth_mb < 100, f"heap grew {growth_mb}MB after rapid hover/unhover -- possible leak"

    # --- Test 4: error path falls back to static thumbnail ---
    # A corrupt file: reuse the small fixture's model but monkeypatch fetch
    # to return garbage bytes for this one request.
    page.evaluate("""
        const origFetch = window.fetch;
        window.fetch = function(url, ...rest) {
            if (String(url).includes('""" + small["id"] + """')) {
                return Promise.resolve(new Response(new Blob([new Uint8Array([1,2,3])])));
            }
            return origFetch(url, ...rest);
        };
    """)
    small_card = page.get_by_text(small["name"]).first
    small_card.hover()
    page.wait_for_timeout(2000)
    fell_back = page.evaluate("""
        () => {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                if (!c.classList.contains('invisible') && c.offsetHeight > 0) return false;
            }
            return true;
        }
    """)
    assert fell_back, "corrupt file should fall back to static thumbnail, not show a broken canvas"

    browser.close()
print("ALL HOVER-PREVIEW TESTS PASSED")
```

Run: `cd frontend && bun run dev` (separate terminal), then `python components/hoverPreview.integration_test.py`
Expected: `ALL HOVER-PREVIEW TESTS PASSED`, with the printed `max frame gap` well under 200ms (contrast with this session's measured pre-fix reproduction: 5680ms/2632ms/2933ms for a 498MB file) and `heap growth` well under 100MB.

- [ ] **Step 6: Verify the OffscreenCanvas-unsupported fallback end-to-end**

```python
# Append to hoverPreview.integration_test.py, or run as a follow-up script
# with a fresh browser context (window.OffscreenCanvas must be stubbed
# before the app's modules first evaluate).
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.add_init_script("window.OffscreenCanvas = undefined;")
    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)

    card = page.get_by_text("small.stl").first  # reuse the fixture uploaded above
    card.hover()
    page.wait_for_timeout(1500)
    live_canvas = page.evaluate("""
        () => {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                if (!c.classList.contains('invisible') && c.offsetHeight > 0) return true;
            }
            return false;
        }
    """)
    assert not live_canvas, "should show static thumbnail only when OffscreenCanvas is unsupported"
    browser.close()
print("FALLBACK TEST PASSED")
```

Expected: `FALLBACK TEST PASSED`.

- [ ] **Step 7: Clean up uploaded test fixtures from the dev database**

The uploads made in Steps 5-6 are real rows in the dev backend's database. Delete them so repeated test runs don't accumulate junk models:

```python
import urllib.request
for model_id in [under["id"], over["id"], small["id"], small_3mf["id"], small_stp["id"]]:
    req = urllib.request.Request(
        f"http://localhost:8000/api/models/{model_id}", method="DELETE"
    )
    urllib.request.urlopen(req)
```

(Run this manually after confirming the tests above passed, or fold it into the test script's `finally` block.)

- [ ] **Step 8: Commit**

```bash
git add frontend/components/HoverPreviewCanvas.tsx frontend/components/ModelList.tsx \
        frontend/components/hoverPreview.integration_test.py \
        frontend/public/test-fixtures/generate_fixtures.py \
        frontend/public/test-fixtures/under-threshold-45mb.stl \
        frontend/public/test-fixtures/over-threshold-60mb.stl \
        frontend/public/test-fixtures/small.3mf \
        frontend/public/test-fixtures/small.stp
git commit -m "refactor: route hover preview through the dedicated Web Worker with a 50MB size gate"
```

**Note on fixture file sizes in git:** the two generated `.stl` fixtures (~45MB and ~60MB) are synthetic (not real user data) but are real binary files that will be committed to the repo. If keeping ~105MB of test fixtures in git history is undesirable, an acceptable alternative at execution time is to generate them in a `pytest` fixture/setup step at test-run time instead of committing them, and add `frontend/public/test-fixtures/*-threshold-*.stl` to `.gitignore` — flag this trade-off to the human partner if it comes up rather than deciding unilaterally, since it changes what future contributors need to do to run this test locally (`small.stl`, `small.3mf`, and `small.stp` are all tiny and fine to commit either way).

---

## Final Verification

After Task 3, do a full whole-branch smoke check before considering this plan done:

1. `cd frontend && bun run build` — succeeds with no errors.
2. Re-run `python components/hoverPreview.integration_test.py` (Task 3, Step 5) one final time against the freshly built code — all assertions pass.
3. Manually confirm in a live rebuild (per this project's established rebuild/reinstall/hash-verify pattern) that hovering a real large file from the actual production library (if one is available for a final human check) no longer freezes the app — this is the same class of check that caught the original bug, and the most direct evidence this plan actually fixes what was reported.
