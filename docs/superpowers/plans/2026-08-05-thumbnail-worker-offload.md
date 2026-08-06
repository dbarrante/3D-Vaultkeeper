# Thumbnail Generation Web Worker Offload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move 3D Vaultkeeper's thumbnail generation (STL/3MF/STEP parsing + Three.js WebGL render + PNG encode) off the browser main thread into a Web Worker with `OffscreenCanvas`, so the background generation loop stops causing brief main-thread hangs.

**Architecture:** A new `frontend/workers/thumbnailWorker.ts` owns all parsing/rendering, communicating via `postMessage`. `frontend/services/thumbnailGenerator.ts` becomes a thin client wrapper with unchanged exported signatures, so its 4 existing call sites need zero changes. STEP parsing (`occt-import-js`) is extracted into a framework-free shared module first, since it's also used by the main-thread hover preview and must not pull `@react-three/fiber` into the worker bundle.

**Tech Stack:** React 19 + TypeScript + Vite 6 + Three.js 0.181 (`STLLoader`, `ThreeMFLoader`) + `occt-import-js` (WASM STEP parser) + native Web Worker / `OffscreenCanvas` APIs.

## Global Constraints

- No frontend automated test framework exists in this project (no vitest/jest) — verification is `bunx tsc --noEmit` for type-correctness plus real Playwright/headless-Chromium scripts for WebGL/runtime correctness, per this project's established convention (see `docs/superpowers/plans/2026-08-05-auto-thumbnails-and-hover-preview.md` for precedent).
- All 4 existing call sites of `thumbnailGenerator.ts`'s exports (`App.tsx:178`, `App.tsx:535`, `App.tsx:661`, `DetailPanel.tsx:196`) must need zero code changes — the public API signatures (`generateThumbnail`, `generateThumbnailFromUrl`, `generateThumbnailFromArrayBuffer`, `ThumbnailTransportError`) stay exactly as they are today.
- The existing `ThumbnailTransportError` vs. generic-render-error distinction must keep working identically (App.tsx's background loop uses `instanceof ThumbnailTransportError` to decide retry-later vs. permanent `thumbnailFailed` quarantine).
- The `renderer.dispose()` + `renderer.forceContextLoss()` WebGL cleanup pattern (a real leak this project already found and fixed once) must be preserved in every code path that creates a renderer.
- Thumbnail output format stays `data:image/png;base64,...` at a fixed 300×300 size, matching every existing consumer (DB `thumbnail` column, `<img src>` usages).

---

### Task 1: Extract STEP geometry parsing into a framework-free shared module

**Files:**
- Create: `frontend/lib/stepGeometry.ts`
- Modify: `frontend/components/STEPLoader.tsx:47-113` (replace `LoadStepFromFile` body, remove now-dead `BuildMesh`)

**Interfaces:**
- Produces: `loadStepGeometryFromBuffer(fileBuffer: ArrayBuffer | Uint8Array): Promise<THREE.Group>` — used by Task 2's worker and by `STEPLoader.tsx`'s `LoadStepFromFile` (both this task and Task 3's fallback path).

**Why this task exists:** `frontend/components/STEPLoader.tsx` imports `useLoader` from `@react-three/fiber` at module scope (line 2). Any file that imports anything from `STEPLoader.tsx` transitively executes that import too. `@react-three/fiber` assumes a DOM/React environment; loading it inside a Web Worker (which has no `window`/`document`) risks throwing at worker-startup time. The actual STEP-parsing logic (`LoadStepFromFile` + `BuildMesh`) never touches `useLoader` — only `LoadStep` (a separate, unrelated export used by `Viewer3D.tsx`) exists in the same file. This task extracts the parsing logic into its own file with zero React dependency, so it's safe to import from a worker.

`BuildMesh`'s `showEdges` parameter is always called with `false` at its only call site (`LoadStepFromFile`, line 63) — the edges branch is dead code and is dropped during this extraction, not preserved.

- [ ] **Step 1: Create the shared module**

```typescript
// frontend/lib/stepGeometry.ts
import * as THREE from "three";
import occtimportjs from "occt-import-js";
import occtWasmUrl from "occt-import-js/dist/occt-import-js.wasm?url";
import occtWorkerUrl from "occt-import-js/dist/occt-import-js-worker.js?url";

function buildMeshFromResult(geometryMesh: any): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();

  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(geometryMesh.attributes.position.array, 3),
  );
  if (geometryMesh.attributes.normal) {
    geometry.setAttribute(
      "normal",
      new THREE.Float32BufferAttribute(geometryMesh.attributes.normal.array, 3),
    );
  }
  geometry.name = geometryMesh.name;
  const index = Uint32Array.from(geometryMesh.index.array);
  geometry.setIndex(new THREE.BufferAttribute(index, 1));

  const defaultMaterial = new THREE.MeshStandardMaterial({
    color: 0x3b82f6,
    roughness: 0.45,
    metalness: 0.1,
    side: THREE.DoubleSide,
  });

  geometry.computeBoundingSphere();
  geometry.computeVertexNormals();

  const mesh = new THREE.Mesh(geometry, defaultMaterial);
  mesh.name = geometryMesh.name;
  mesh.frustumCulled = false;
  return mesh;
}

// Deliberately framework-free (no @react-three/fiber import) so this module
// is safe to import from a Web Worker, which has no `window`/`document` and
// would risk throwing at import time if a module pulled in React-adjacent
// code. Shared by frontend/components/STEPLoader.tsx's LoadStepFromFile
// (main thread: hover preview) and frontend/workers/thumbnailWorker.ts
// (worker thread: background thumbnail generation).
export async function loadStepGeometryFromBuffer(
  fileBuffer: ArrayBuffer | Uint8Array,
): Promise<THREE.Group> {
  const initOcct = (await import("occt-import-js")).default;
  const occt = await initOcct({
    locateFile: (file: string) => {
      if (file.endsWith(".wasm")) return occtWasmUrl;
      if (file.endsWith(".worker.js")) return occtWorkerUrl;
      return file;
    },
  });

  const fileIntBuffer = new Uint8Array(fileBuffer);
  const result = occt.ReadStepFile(fileIntBuffer, null);

  const group = new THREE.Group();
  for (const resultMesh of result.meshes) {
    const mesh = buildMeshFromResult(resultMesh);
    mesh.scale.set(1.0, 1.0, 1.0);
    group.add(mesh);
  }
  return group;
}
```

- [ ] **Step 2: Delegate `LoadStepFromFile` to the shared module and remove dead code**

In `frontend/components/STEPLoader.tsx`, replace lines 47-113 (the full `LoadStepFromFile` function and the `BuildMesh` function after it) with:

```typescript
export async function LoadStepFromFile(fileBuffer) {
  return loadStepGeometryFromBuffer(fileBuffer);
}
```

Add the import at the top of the file: `import { loadStepGeometryFromBuffer } from "@/lib/stepGeometry";`. Leave `LoadStep` (lines 7-45, the URL-based export used by `Viewer3D.tsx`) completely untouched — it's out of scope for this plan.

- [ ] **Step 3: Verify the extraction compiles clean**

Run: `cd frontend && bunx tsc --noEmit`
Expected: the same pre-existing baseline errors as before this change (`App.tsx:1104`, `ModelList.tsx:493/888/946`, `STEPLoader.tsx:4-5`'s `?url` import warnings) and no *new* errors involving `stepGeometry.ts` or `STEPLoader.tsx`'s `LoadStepFromFile`/`BuildMesh`.

- [ ] **Step 4: Verify the hover preview's STEP rendering still works identically**

`frontend/components/HoverPreviewCanvas.tsx:102` calls `LoadStepFromFile` directly — this is the one real consumer of the code just moved, and it must keep working exactly as before.

1. Start the dev server: `cd frontend && bun run dev` (note the port it prints).
2. Start the backend against a throwaway DB: `cd backend && DISABLE_SCHEDULER=1 python -m uvicorn app.main:app --port 8000` (a fresh `app.db`-relative SQLite file is fine; this is a disposable verification run, not production data).
3. Upload a STEP file through the running app (drag-and-drop or the upload button) — if no real STEP file is handy, `frontend/node_modules/occt-import-js/test/testfiles/cax-if/as1-oc-214.stp` in this repo is a real, valid sample already available locally.
4. Hover over the uploaded model's grid card for over 400ms (the hover-preview trigger delay) and confirm a live rotating 3D preview renders, matching pre-change behavior (a blank card or a thrown console error means the extraction broke something).

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/stepGeometry.ts frontend/components/STEPLoader.tsx
git commit -m "refactor: extract STEP geometry parsing into a framework-free shared module"
```

---

### Task 2: Build the thumbnail Web Worker

**Files:**
- Create: `frontend/lib/thumbnailScene.ts`
- Create: `frontend/workers/thumbnailWorker.ts`

**Interfaces:**
- Consumes: `loadStepGeometryFromBuffer` (Task 1).
- Produces: `buildThumbnailScene(object: THREE.Object3D): { scene: THREE.Scene; camera: THREE.PerspectiveCamera }` — used by this task's worker and by Task 3's fallback path, so the scene/camera/light setup (identical in both contexts) exists in exactly one place instead of being duplicated between the worker and the fallback. Also produces the worker's message contract, consumed by Task 3's client wrapper:
  ```typescript
  type WorkerRequest =
    | { reqId: number; kind: "url"; url: string; filename: string }
    | { reqId: number; kind: "blob"; blob: Blob; filename: string };

  type WorkerResponse =
    | { reqId: number; ok: true; thumbnail: string }
    | { reqId: number; ok: false; kind: "transport" | "render"; message: string };
  ```
  The worker is a standalone file with no exports (a worker entry point communicates only via `postMessage`) — Task 3 constructs it via `new Worker(new URL("../workers/thumbnailWorker.ts", import.meta.url), { type: "module" })` and relies on this exact message shape.

This task is independently testable: the worker can be driven directly from a page via `new Worker(...)` + `postMessage`, without any changes to the rest of the app yet.

- [ ] **Step 1: Extract the shared scene-building helper**

The scene/camera/light setup below is identical whether it runs in the worker (this task) or the main-thread fallback (Task 3) — only renderer construction and image encoding differ between those two contexts. Extracting it once here avoids ~30 lines of verbatim duplication between the two files.

```typescript
// frontend/lib/thumbnailScene.ts
import * as THREE from "three";

// Framework-free (pure THREE.js object construction, no canvas/renderer/DOM
// dependency) so this module is safe to import from both a Web Worker
// (frontend/workers/thumbnailWorker.ts) and the main thread (the
// synchronous fallback in frontend/services/thumbnailGenerator.ts).
export function buildThumbnailScene(
  object: THREE.Object3D,
): { scene: THREE.Scene; camera: THREE.PerspectiveCamera } {
  const scene = new THREE.Scene();
  const box = new THREE.Box3();
  box.setFromObject(object);
  scene.add(object);

  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
  camera.up.set(0.0, -1.0, 0.0);

  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());

  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = camera.fov * (Math.PI / 180);
  let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
  cameraZ *= 3.5; // Zoom out slightly for padding -- matches the pre-worker value
  camera.position.set(center.x, center.y, cameraZ);

  const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
  scene.add(ambientLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
  dirLight.position.set(camera.position.x, camera.position.y, camera.position.z);
  dirLight.lookAt(center);
  scene.add(dirLight);

  const backLight = new THREE.DirectionalLight(0xffffff, 0.5);
  backLight.position.set(-5, -5, -10);
  scene.add(backLight);

  camera.lookAt(center);

  return { scene, camera };
}
```

- [ ] **Step 2: Write the worker**

```typescript
// frontend/workers/thumbnailWorker.ts
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { loadStepGeometryFromBuffer } from "../lib/stepGeometry";
import { buildThumbnailScene } from "../lib/thumbnailScene";

type WorkerRequest =
  | { reqId: number; kind: "url"; url: string; filename: string }
  | { reqId: number; kind: "blob"; blob: Blob; filename: string };

type WorkerResponse =
  | { reqId: number; ok: true; thumbnail: string }
  | { reqId: number; ok: false; kind: "transport" | "render"; message: string };

class ThumbnailTransportErrorInWorker extends Error {}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function renderObjectToDataUrl(object: THREE.Object3D): Promise<string> {
  const { scene, camera } = buildThumbnailScene(object);

  const canvas = new OffscreenCanvas(300, 300);
  const renderer = new THREE.WebGLRenderer({
    canvas: canvas as unknown as HTMLCanvasElement,
    alpha: true,
    antialias: true,
    preserveDrawingBuffer: true,
  });
  // updateStyle defaults to true, which makes Three.js write to
  // canvas.style -- OffscreenCanvas has no .style property (it's not a DOM
  // element) and setSize() throws without this false argument.
  renderer.setSize(300, 300, false);
  renderer.render(scene, camera);

  // OffscreenCanvas has no toDataURL() (that's HTMLCanvasElement-only) --
  // convertToBlob() is its async equivalent. It must be awaited BEFORE
  // dispose()/forceContextLoss() below: unlike the old synchronous
  // toDataURL() call, convertToBlob() needs a live WebGL context to read
  // pixels from, so disposing first would produce a blank/broken image.
  const blob = await canvas.convertToBlob({ type: "image/png" });
  const dataUrl = await blobToDataUrl(blob);

  renderer.dispose();
  // dispose() only frees Three.js-side GPU resources -- it does NOT release
  // the underlying WebGL context. Without forceContextLoss(), each renderer
  // this worker creates (one per thumbnail request, for the worker's whole
  // lifetime) leaves a live context behind, and the browser's hard cap on
  // simultaneous contexts gets hit within under a minute -- the same bug
  // already found and fixed once in this codebase's pre-worker code.
  renderer.forceContextLoss();

  return dataUrl;
}

async function renderModelToDataUrl(blob: Blob, filename: string): Promise<string> {
  const contents = await blob.arrayBuffer();
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

  const dataUrl = await renderObjectToDataUrl(object);

  if (isSTL) {
    (object as THREE.Mesh).geometry.dispose();
    ((object as THREE.Mesh).material as THREE.Material).dispose();
  }

  return dataUrl;
}

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const msg = event.data;
  try {
    let blob: Blob;
    if (msg.kind === "url") {
      const response = await fetch(msg.url);
      if (!response.ok) {
        // Do NOT parse a non-ok response body as model bytes -- it's
        // JSON/HTML error content, not real file bytes.
        throw new ThumbnailTransportErrorInWorker(
          `Failed to fetch model file for thumbnail generation (${response.status} ${response.statusText}): ${msg.url}`,
        );
      }
      blob = await response.blob();
    } else {
      blob = msg.blob;
    }

    const thumbnail = await renderModelToDataUrl(blob, msg.filename);
    const response: WorkerResponse = { reqId: msg.reqId, ok: true, thumbnail };
    (self as unknown as Worker).postMessage(response);
  } catch (err) {
    const isTransport = err instanceof ThumbnailTransportErrorInWorker;
    const response: WorkerResponse = {
      reqId: msg.reqId,
      ok: false,
      kind: isTransport ? "transport" : "render",
      message: err instanceof Error ? err.message : String(err),
    };
    (self as unknown as Worker).postMessage(response);
  }
};
```

- [ ] **Step 3: Verify it compiles clean**

Run: `cd frontend && bunx tsc --noEmit`
Expected: same pre-existing baseline errors as Task 1's Step 3, no new errors from `thumbnailScene.ts` or `thumbnailWorker.ts`.

- [ ] **Step 4: Verify the worker in isolation with a real render**

No app wiring exists yet (that's Task 3) — drive the worker directly from a throwaway HTML page served by the dev server, so this task's correctness doesn't depend on unfinished later tasks.

1. Start the dev server: `cd frontend && bun run dev` (note the port).
2. Write this Python script (adjust the port to match) and run it:

```python
# scratch: verify_worker.py
import base64
from playwright.sync_api import sync_playwright

CUBE_STL = """solid cube
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 10 10 0
    vertex 10 0 0
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 0 10 0
    vertex 10 10 0
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 10
    vertex 10 0 10
    vertex 10 10 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 10
    vertex 10 10 10
    vertex 0 10 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 10 0 0
    vertex 10 0 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 10 0 10
    vertex 0 0 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 10 0
    vertex 10 10 10
    vertex 10 10 0
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 10 0
    vertex 0 10 10
    vertex 10 10 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 0 10 10
    vertex 0 10 0
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 0 0 10
    vertex 0 10 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 10 0 0
    vertex 10 10 0
    vertex 10 10 10
  endloop
endfacet
facet normal 0 0 0
  outer loop
    vertex 10 0 0
    vertex 10 10 10
    vertex 10 0 10
  endloop
endfacet
endsolid cube
"""

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:5173/")  # adjust port to match dev server
    page.wait_for_load_state("networkidle")

    result = page.evaluate(
        """
        async (stlText) => {
            const worker = new Worker(
                new URL("/workers/thumbnailWorker.ts", window.location.origin),
                { type: "module" }
            );
            const blob = new Blob([stlText], { type: "text/plain" });
            const response = await new Promise((resolve) => {
                worker.onmessage = (e) => resolve(e.data);
                worker.postMessage({ reqId: 1, kind: "blob", blob, filename: "cube.stl" });
            });
            worker.terminate();
            return response;
        }
        """,
        CUBE_STL,
    )
    browser.close()

assert result["ok"] is True, f"worker returned an error: {result}"
thumbnail = result["thumbnail"]
assert thumbnail.startswith("data:image/png;base64,"), "unexpected thumbnail format"

png_bytes = base64.b64decode(thumbnail.split(",", 1)[1])
with open(r"C:\Users\dkbar\AppData\Local\Temp\claude\worker_cube_test.png", "wb") as f:
    f.write(png_bytes)
print("PASS -- saved to worker_cube_test.png, view it to confirm it's a rendered cube, not blank")
```

3. Run it, then use the Read tool on the saved PNG to visually confirm it shows an actual rendered cube (visible shading/edges), not a blank or solid-color image.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/thumbnailScene.ts frontend/workers/thumbnailWorker.ts
git commit -m "feat: add Web Worker for off-main-thread thumbnail generation"
```

---

### Task 3: Rewrite the client wrapper with worker dispatch, concurrency, and fallback

**Files:**
- Modify: `frontend/services/thumbnailGenerator.ts` (full rewrite)

**Interfaces:**
- Consumes: Task 2's worker (`frontend/workers/thumbnailWorker.ts`) and its message contract; Task 2's `buildThumbnailScene` (for the fallback path's scene/camera/light setup); Task 1's `loadStepGeometryFromBuffer` (for the fallback path's STEP parsing).
- Produces: `generateThumbnail(file: File): Promise<string>`, `generateThumbnailFromUrl(url: string, filename: string): Promise<string>`, `generateThumbnailFromArrayBuffer(contents: ArrayBuffer, filename: string): Promise<string>`, `class ThumbnailTransportError extends Error` — identical signatures to what exists today, consumed unchanged by `App.tsx:178,535,661` and `DetailPanel.tsx:196`.

- [ ] **Step 1: Replace the file's contents**

```typescript
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
  { resolve: (v: string) => void; reject: (e: Error) => void }
>();

function failAllPending(message: string) {
  for (const entry of pending.values()) {
    entry.reject(new Error(message));
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
      if (msg.ok) {
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
      // synchronous main-thread path below.
      failAllPending("Thumbnail worker crashed");
      workerInitFailed = true;
      worker = null;
    };
    worker = w;
    return worker;
  } catch {
    workerInitFailed = true;
    return null;
  }
}

function requestFromWorker(
  activeWorker: Worker,
  request: WorkerRequest,
): Promise<string> {
  return new Promise((resolve, reject) => {
    pending.set(request.reqId, { resolve, reject });
    activeWorker.postMessage(request);
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
```

- [ ] **Step 2: Verify it compiles clean**

Run: `cd frontend && bunx tsc --noEmit`
Expected: same pre-existing baseline errors as Task 1's Step 3, no new errors from `thumbnailGenerator.ts`.

- [ ] **Step 3: Verify the build succeeds**

Run: `cd frontend && bun run build`
Expected: clean build (the existing "chunks larger than 500kB" warning is pre-existing and expected; no new errors).

- [ ] **Step 4: Verify all 4 call sites end-to-end against the real dev app**

1. Start the backend (`cd backend && DISABLE_SCHEDULER=1 python -m uvicorn app.main:app --port 8000`, a throwaway DB) and frontend (`cd frontend && bun run dev`).
2. **Upload flow** (`App.tsx:535`): upload a real STL file through the UI. Confirm the new model appears with a real (non-blank) thumbnail almost immediately, not just after the background loop's next tick.
3. **File-replace flow** (`DetailPanel.tsx:196`): open that model's detail panel, replace its file with a different STL. Confirm the thumbnail updates to match the new file.
4. **Background loop** (`App.tsx:178`): upload a second model, then delete its thumbnail via `PATCH /api/models/{id}` with `{"thumbnail": null}` (or upload without letting a thumbnail generate). Wait up to ~3-6 seconds and confirm the background loop picks it up and fills in a thumbnail without any page interaction.
5. Open the browser dev console throughout steps 2-4 and confirm no uncaught errors.

- [ ] **Step 5: Verify the fallback path works when OffscreenCanvas is unsupported**

```python
# scratch: verify_fallback.py
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    # Stub out OffscreenCanvas BEFORE any app script runs, so
    # thumbnailGenerator.ts's getWorker() sees it as unsupported.
    page.add_init_script("window.OffscreenCanvas = undefined;")
    page.goto("http://127.0.0.1:5173/")  # adjust port to match dev server
    page.wait_for_load_state("networkidle")

    result = page.evaluate(
        """
        async () => {
            const mod = await import("/services/thumbnailGenerator.ts");
            const stlText = `solid cube
facet normal 0 0 0
  outer loop
    vertex 0 0 0
    vertex 10 10 0
    vertex 10 0 0
  endloop
endfacet
endsolid cube
`;
            const file = new File([stlText], "cube.stl");
            const thumb = await mod.generateThumbnail(file);
            return thumb.startsWith("data:image/png;base64,");
        }
        """
    )
    browser.close()

assert result is True, "fallback path did not produce a valid thumbnail"
print("PASS -- fallback path works with OffscreenCanvas stubbed out")
```

Note: this minimal one-triangle STL is enough to prove the fallback pipeline runs end-to-end (parse → render → encode) without throwing; it doesn't need to be a full cube like Task 2's since this test only checks the fallback code path executes, not visual correctness (already covered in Task 2's Step 3).

- [ ] **Step 6: Verify the transport-vs-render error distinction survives the worker boundary**

The background loop in `App.tsx` depends on telling these two failure kinds apart (`instanceof ThumbnailTransportError` means "retry later," anything else means "permanently quarantine via `thumbnailFailed`") — this must keep working now that the error originates inside the worker and crosses a `postMessage` boundary before `App.tsx` ever sees it.

```python
# scratch: verify_error_distinction.py
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:5173/")  # adjust port to match dev server
    page.wait_for_load_state("networkidle")

    result = page.evaluate(
        """
        async () => {
            const mod = await import("/services/thumbnailGenerator.ts");
            const out = {};

            // Transport failure: a URL that 404s.
            try {
                await mod.generateThumbnailFromUrl("http://127.0.0.1:5173/does-not-exist.stl", "x.stl");
                out.transportThrew = false;
            } catch (e) {
                out.transportThrew = true;
                out.transportIsTransportError = e instanceof mod.ThumbnailTransportError;
            }

            // Render failure: deterministic via an unsupported extension,
            // rather than relying on STLLoader's parsing leniency toward
            // garbage input (which isn't guaranteed to throw).
            const badFile = new File([new Blob(["irrelevant content"])], "not-a-model.pdf");
            try {
                await mod.generateThumbnail(badFile);
                out.renderThrew = false;
            } catch (e) {
                out.renderThrew = true;
                out.renderIsTransportError = e instanceof mod.ThumbnailTransportError;
            }

            return out;
        }
        """
    )
    browser.close()

assert result["transportThrew"], "expected the 404 fetch to throw"
assert result["transportIsTransportError"], f"expected a ThumbnailTransportError, got: {result}"
assert result["renderThrew"], "expected the unsupported extension to throw"
assert not result["renderIsTransportError"], f"unsupported-extension error should NOT be a ThumbnailTransportError: {result}"
print("PASS -- transport vs render error distinction works across the worker boundary")
```

- [ ] **Step 7: Commit**

```bash
git add frontend/services/thumbnailGenerator.ts
git commit -m "refactor: route thumbnail generation through the Web Worker with a synchronous fallback"
```

---

### Task 4: Verify the actual bug is fixed — main-thread responsiveness and no context leak

**Files:** none (verification only — this task produces no code changes).

**Interfaces:**
- Consumes: the fully-wired app from Task 3.

This is the acceptance test for the reason this plan exists: confirm the main thread no longer blocks during active background thumbnail generation, and confirm the worker's WebGL cleanup doesn't leak contexts across many sequential generations (mirroring the leak this codebase already found and fixed once elsewhere).

- [ ] **Step 1: Seed several thumbnail-less models**

With the dev server and a throwaway backend running (per Task 3 Step 4), upload at least 5 STL files without letting them get thumbnails first (upload via a raw `POST /api/models/upload` call with no `thumbnail` field, so the background loop has real work queued when the next test starts). A quick way: use the cube STL text from Task 2's Step 3, uploaded 5 times under different filenames.

- [ ] **Step 2: Run the main-thread responsiveness test**

```python
# scratch: verify_responsiveness.py
import time
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:5173/")  # adjust port to match dev server
    page.wait_for_load_state("networkidle")

    # A main-thread heartbeat via requestAnimationFrame -- if the main
    # thread ever blocks for a real chunk of time (the bug this plan
    # fixes), the frame rate visibly drops during that window.
    page.evaluate(
        """
        window.__heartbeat = 0;
        function tick() { window.__heartbeat++; requestAnimationFrame(tick); }
        requestAnimationFrame(tick);
        """
    )

    samples = []
    last = page.evaluate("window.__heartbeat")
    for _ in range(100):  # 100 * 200ms = 20s, long enough to span several 3s ticks
        time.sleep(0.2)
        current = page.evaluate("window.__heartbeat")
        samples.append(current - last)
        last = current

    browser.close()

print("frames per 200ms window (min/avg):", min(samples), sum(samples) / len(samples))
print("all samples:", samples)
# At 60fps, an unblocked main thread gets ~12 frames per 200ms window. A
# worst-case sample near 0 means the main thread froze for most of that
# window -- the exact symptom this plan fixes. Some variance is expected
# (headless Chromium, background loop's own DOM updates); a healthy result
# has no window collapsing to 0-1 frames while the background loop is
# actively generating.
```

Run it while step 1's uploaded models still lack thumbnails (so the background loop is actively working during the 20s window). Confirm no sample collapses to 0-1 frames — a smoking-gun main-thread freeze.

- [ ] **Step 3: Run the WebGL context-leak test**

```python
# scratch: verify_no_context_leak.py
from playwright.sync_api import sync_playwright

CUBE_STL = "solid cube\nfacet normal 0 0 0\n  outer loop\n    vertex 0 0 0\n    vertex 10 10 0\n    vertex 10 0 0\n  endloop\nendfacet\nendsolid cube\n"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:5173/")  # adjust port to match dev server
    page.wait_for_load_state("networkidle")

    # Trigger many sequential worker-based generations back-to-back
    # (bypassing the real 3s tick delay) by calling the client wrapper
    # directly. Chromium's per-page hard cap on simultaneous WebGL contexts
    # is commonly well under 30 -- if forceContextLoss() weren't working
    # inside the worker (Task 2, Step 1), some of these would start failing
    # partway through.
    results = page.evaluate(
        """
        async (stlText) => {
            const mod = await import("/services/thumbnailGenerator.ts");
            const out = [];
            for (let i = 0; i < 30; i++) {
                try {
                    const file = new File([stlText], `cube${i}.stl`);
                    const thumb = await mod.generateThumbnail(file);
                    out.push(thumb.startsWith("data:image/png;base64,"));
                } catch (e) {
                    out.push(false);
                }
            }
            return out;
        }
        """,
        CUBE_STL,
    )
    browser.close()

assert all(results), f"some generations failed -- possible context leak: {results}"
print(f"PASS -- all {len(results)} sequential generations succeeded, no context leak")
```

- [ ] **Step 4: Report results**

No code changes in this task, so no commit — record the two scripts' output (min/avg frames-per-window, and the all-30-passed confirmation) as the plan's closing verification evidence.
