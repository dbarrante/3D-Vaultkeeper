# Thumbnail Generation Web Worker Offload — Design

## Problem

3D Vaultkeeper's background thumbnail generation loop (`App.tsx`'s `tick()`, firing every 3 seconds while any model lacks a thumbnail) causes the whole app to briefly hang during active generation. Root cause, confirmed live this session:

- `frontend/services/thumbnailGenerator.ts`'s `renderObjectToDataUrl()` runs a real synchronous `THREE.WebGLRenderer.render()` call plus `canvas.toDataURL("image/png")` on the main JS thread.
- `generateThumbnailFromArrayBuffer()` also runs STL/3MF parsing (`STLLoader.parse()` / `ThreeMFLoader.parse()`) and STEP loading (`LoadStepFromFile`, via `occt-import-js`) synchronously on the main thread before that render call.
- For a large or detailed mesh, this blocks the browser's UI thread for the duration of parsing + rendering + encoding — long enough to be felt as a "brief hang," recurring on the loop's ~3s cadence.
- A 45-second continuous poll of a cheap backend endpoint (`/api/folders`) during active generation stayed flat at 3–33ms latency throughout, confirming the backend never stalls — this is purely a main-thread blocking problem in the browser.

## Goal

Move all thumbnail generation (parsing + WebGL render + PNG encode) off the main thread into a Web Worker with `OffscreenCanvas`, so the background loop (and any other trigger of thumbnail generation) never blocks the UI.

## Scope

All four current call sites of `frontend/services/thumbnailGenerator.ts`'s exported functions move to the worker-backed implementation, with no call-site changes required:

1. `App.tsx:178` — the background generation loop (`generateThumbnailFromUrl`) — the one causing the reported hang.
2. `App.tsx:535` — upload flow (`generateThumbnail`, File-based).
3. `App.tsx:661` — a second upload-adjacent flow (`generateThumbnail`, File-based).
4. `DetailPanel.tsx:196` — file-replace flow (`generateThumbnail`, File-based).

All four already share the same underlying functions, so routing all of them through the worker is no additional work beyond routing just the background loop, and avoids maintaining two parallel rendering implementations.

## Architecture

### New file: `frontend/workers/thumbnailWorker.ts`

A dedicated Web Worker (Vite module worker) containing the rendering pipeline currently in `thumbnailGenerator.ts`, relocated and adapted for a worker context:

- STL/3MF parsing: `STLLoader.parse()` / `ThreeMFLoader.parse()` — unchanged, these operate on `ArrayBuffer`s with no DOM dependency and work identically in a worker.
- STEP parsing: the `LoadStepFromFile` logic currently in `frontend/components/STEPLoader.tsx` — `occt-import-js` is pure WASM/JS (Emscripten-compiled), with no DOM dependency, and works unchanged in a worker. The existing `occtWasmUrl`/`occtWorkerUrl` (`?url` Vite imports) resolution stays the same.
- Scene/camera/light setup and the render call: same logic as today's `renderObjectToDataUrl()`, except the `WebGLRenderer` is constructed against `new OffscreenCanvas(300, 300)` instead of an implicit DOM `<canvas>` — no DOM canvas is ever created or transferred, since nothing needs to be displayed live.
- Final image encoding: `OffscreenCanvas` has no `toDataURL()` (that's an `HTMLCanvasElement`-only API). Instead: `await offscreenCanvas.convertToBlob({ type: "image/png" })` → `Blob`, then `FileReader.readAsDataURL(blob)` (available in worker global scope) to produce the same `data:image/png;base64,...` string format the rest of the app already expects (the DB's `thumbnail` column, `<img src>` usages, etc. are all unchanged).
- WebGL cleanup: the existing `renderer.dispose()` + `renderer.forceContextLoss()` pattern (fixed earlier this session as a real context-leak bug) is preserved verbatim, now inside the worker's render function instead of the main thread's.

### Modified file: `frontend/services/thumbnailGenerator.ts`

Becomes a thin main-thread client wrapper. Keeps its existing exported signatures unchanged:

```ts
export class ThumbnailTransportError extends Error { ... }  // unchanged
export const generateThumbnailFromArrayBuffer = async (contents: ArrayBuffer, filename: string): Promise<string> => { ... }
export const generateThumbnailFromUrl = async (url: string, filename: string): Promise<string> => { ... }
export const generateThumbnail = async (file: File): Promise<string> => { ... }
```

Internally, each function now:

1. Lazily creates a singleton `Worker` on first use: `new Worker(new URL("../workers/thumbnailWorker.ts", import.meta.url), { type: "module" })`.
2. Posts a request message and returns a `Promise` that resolves/rejects when the matching response arrives.
3. Falls back to the current synchronous main-thread implementation (kept as a private, non-exported function in the same file) if `typeof OffscreenCanvas === "undefined"`, or if worker creation / the first request throws — so the app degrades gracefully rather than breaking outright on an unsupported WebView2 runtime. This matters because the app is meant to be sold to run on other people's machines, not just this one.

### Message contract

Main → worker:

```ts
type WorkerRequest =
  | { reqId: number; kind: "url"; url: string; filename: string }
  | { reqId: number; kind: "blob"; blob: Blob; filename: string };
```

`File`/`Blob` objects structured-clone directly through `postMessage` with no explicit `Transferable` handling needed, and both support `.arrayBuffer()` in worker scope. So `generateThumbnail(file: File)` posts the `File` straight through (it's already a `Blob`), and `generateThumbnailFromArrayBuffer(contents: ArrayBuffer, filename)` wraps its input as `new Blob([contents])` before posting — giving the worker exactly one code path ("parse this blob of bytes as this filename") regardless of which public function was called, instead of a separate ArrayBuffer-transfer path to maintain.

Worker → main:

```ts
type WorkerResponse =
  | { reqId: number; ok: true; thumbnail: string }
  | { reqId: number; ok: false; kind: "transport" | "render"; message: string };
```

The `kind: "transport" | "render"` distinction preserves the existing behavior the background loop already depends on: a transport failure (e.g. a reference-mode model's source file temporarily unreachable, surfacing as a non-ok fetch response) means "retry later," while a render/parse failure means "permanently quarantine via `thumbnailFailed`." The client wrapper reconstructs a real `ThumbnailTransportError` instance on the main thread when `kind === "transport"`, so existing `instanceof ThumbnailTransportError` checks in `App.tsx` keep working with zero changes there.

### Concurrency

The client wrapper tracks in-flight requests in a `Map<number, { resolve, reject }>` keyed by an incrementing `reqId`, so multiple concurrent calls (e.g. a manual upload firing while the background loop is mid-tick) resolve correctly regardless of response order. Today's code is only accidentally safe here because it's strictly sequential (one tick, awaited fully, before the next); moving to a worker removes that accidental guarantee, so the client wrapper must handle real concurrency explicitly.

## Error Handling

- Transport failures (non-ok fetch response) inside the worker throw a worker-local marker that the worker's message handler catches and reports as `{ ok: false, kind: "transport", message }` — never attempting to parse a non-ok response body as model file bytes (same guard as today).
- Any other exception (parse failure, corrupt file, WebGL error) is reported as `{ ok: false, kind: "render", message }`.
- If the worker itself fails to initialize (missing `OffscreenCanvas`, WASM load failure, etc.), the client wrapper's fallback path takes over per-call — the app never surfaces a hard failure purely due to environment support.

## Testing

Consistent with this project's established rule that WebGL-adjacent changes need real Playwright/headless-Chromium verification, not just type-checking:

1. **Visual correctness**: worker-generated thumbnails for representative STL, 3MF, and STEP files are non-blank and structurally correct (screenshot/pixel inspection), matching today's main-thread output.
2. **Main-thread responsiveness**: a heartbeat-counter test (same technique already used to verify `scheduler.py`'s `asyncio.to_thread` fix) confirms the main thread keeps processing during active worker-based generation — this is the actual acceptance criterion for the bug this design fixes.
3. **Error-path behavior**: simulate a 404/non-ok fetch to confirm the transport-vs-render distinction still drives retry-later vs. permanent-quarantine correctly.
4. **No WebGL context leak**: many sequential worker-based generations in a row don't exhaust the browser's context limit (mirrors the earlier context-leak bug already found and fixed once in this same code this session).
5. **Fallback path**: with `OffscreenCanvas` stubbed out as `undefined`, confirm thumbnail generation still succeeds via the synchronous fallback.

## Out of Scope

- Any change to the thumbnail-queue backend logic (already fixed separately this session).
- Any change to the hover-preview 3D canvas (`HoverPreviewCanvas.tsx`) or the detail-panel live viewer — those remain on the main thread, unaffected by this change.
- A worker pool / parallel multi-thumbnail generation — the background loop is intentionally sequential (one model every 3s) and this design preserves that pacing; only the *location* of the work changes, not its cadence or concurrency model.
