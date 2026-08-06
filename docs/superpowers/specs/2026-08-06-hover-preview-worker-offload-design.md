# Hover-Preview Web Worker Offload — Design

## Problem

`frontend/components/HoverPreviewCanvas.tsx` (the live, auto-rotating 3D preview shown when hovering a model card) fetches and parses the entire model file, then builds and renders a Three.js scene, all synchronously on the main thread. Root cause confirmed this session via a live Playwright reproduction driving the packaged app's real backend against Dave's actual ~3449-model library:

- Dave's library contains real files up to 498MB (median file size is 1.9MB — these are rare but real outliers).
- Hovering a 498MB STL froze the main thread for 5.68s, then 2.63s, then 2.93s in successive frames, and JS heap spiked from 247MB to 2.4GB before settling around 820MB (up from a ~235MB baseline).
- There is already a 400ms hover-intent debounce in `frontend/components/ModelList.tsx` (`onMouseEnter` defers `setHoveredPreviewModelId` via `setTimeout`, cleared on `onMouseLeave`), so this is not a scroll-triggered fetch storm — it takes a genuine sustained hover to trigger.
- There is no `AbortController`: moving the mouse off a card before fetch/parse completes does not cancel the in-flight work.

This is architecturally the same class of bug this session's `thumbnailWorker.ts` offload already fixed for background thumbnail generation (see `docs/superpowers/specs/2026-08-05-thumbnail-worker-offload-design.md`), but that fix never touched this code path — the hover preview was explicitly out of scope there.

## Goal

Move hover-preview parsing and rendering off the main thread into a dedicated Web Worker, and add a file-size threshold above which the live preview is skipped entirely in favor of the existing static thumbnail/icon — so no hover, of any file, can ever block the main thread or blow up memory the way the 498MB reproduction did.

## Why not reuse `thumbnailWorker.ts`

That worker is one-shot request→response: parse, render once, encode to PNG, done, matched via a `reqId` map. Hover-preview is a fundamentally different shape — parse once, then render *continuously* for as long as the user keeps hovering, with mid-flight cancellation when they stop. Forcing both into one protocol would tangle a simple request/response state machine with a live-session one. `frontend/lib/stepGeometry.ts` (STEP parsing) is still reused directly; `frontend/lib/thumbnailScene.ts` is not — hover-preview's scene setup (sphere-based camera fit so the object can't clip at any rotation angle, a pivot group for in-place spinning, tighter framing for a dedicated preview area) is already meaningfully different from the static thumbnail's fixed-orientation framing, and forcing a shared abstraction across both would cost more than it saves.

## Architecture

### New file: `frontend/workers/hoverPreviewWorker.ts`

A dedicated Web Worker (Vite module worker, `{ type: "module" }` per the existing `vite.config.ts` `worker: { format: "es" }` setting). Owns the full pipeline for one live session at a time:

- Fetches the model file itself (`fetch(url)` → `arrayBuffer()`), matching `thumbnailWorker.ts`'s convention of the worker owning fetch rather than the client pre-fetching and transferring bytes.
- Parses STL (`STLLoader.parse()`) / 3MF (`ThreeMFLoader.parse()`) inline, or STEP via the shared `loadStepGeometryFromBuffer` from `stepGeometry.ts`.
- Builds the scene using the same framing/lighting/pivot logic `HoverPreviewCanvas.tsx` has today (sphere-radius camera-distance solve, recentering pivot group, ambient + key + back lights) — relocated into the worker essentially verbatim.
- Renders to an `OffscreenCanvas` obtained via `canvas.transferControlToOffscreen()` on the transferred `<canvas>` element (sent once, in the `start` message's transfer list) — the worker owns a real `requestAnimationFrame` loop rendering directly to that canvas. No per-frame `postMessage` traffic; this is the standard technique for real-time worker-side rendering and keeps steady-state overhead near zero.
- On `cancel`, stops the rAF loop and disposes resources: `geometry.dispose()` / `material.dispose()` on every mesh in the object graph (matching `disposeObject3D` in today's `HoverPreviewCanvas.tsx`), then `renderer.dispose()` + `renderer.forceContextLoss()` — the same WebGL-cleanup convention established earlier this session for both the thumbnail worker and the existing hover-preview code.

Only one session is ever live in the worker at a time (the UI only ever shows one hover-preview). A `start` message implicitly supersedes and cancels whatever session is currently running before beginning the new one.

### Message contract

Main → worker:

```ts
type HoverWorkerRequest =
  | { type: "start"; sessionId: number; canvas: OffscreenCanvas; url: string; name: string }
  | { type: "cancel"; sessionId: number };
```

`canvas` is included in the `postMessage` transfer list (zero-copy transfer of the `OffscreenCanvas`, obtained from `canvas.transferControlToOffscreen()` on the client side).

Worker → main:

```ts
type HoverWorkerResponse =
  | { type: "ready"; sessionId: number }
  | { type: "error"; sessionId: number; message: string };
```

Exactly one `ready` or `error` message is sent per session, right after the first successful `renderer.render()` call (for `ready`) or on any fetch/parse/WebGL failure (for `error`). No further messages are sent for that session afterward — the rAF loop runs entirely worker-side with no per-frame traffic. The client wrapper ignores any `ready`/`error` whose `sessionId` no longer matches the current session (superseded by a later `start` or already cancelled).

### New file: `frontend/services/hoverPreviewClient.ts`

A thin client wrapper, mirroring `thumbnailGenerator.ts`'s singleton-worker pattern:

- Lazily creates a singleton `Worker` on first use.
- Exposes a single function: `startHoverPreview(canvas: HTMLCanvasElement, model: STLModel, callbacks: { onReady: () => void; onError: () => void }): { cancel: () => void }`.
- Internally: calls `canvas.transferControlToOffscreen()`, posts `{ type: "start", sessionId, canvas, url, name }` with the `OffscreenCanvas` in the transfer list, tracks the current `sessionId`, and returns a `cancel()` closure that posts `{ type: "cancel", sessionId }`.
- If `typeof OffscreenCanvas === "undefined"` or worker construction throws, `startHoverPreview` synchronously calls `onError()` and returns a no-op `cancel()` — no synchronous main-thread fallback (see Fallback Behavior below).

### Modified file: `frontend/components/HoverPreviewCanvas.tsx`

Rewritten to:

1. Gate on size: if `model.size > HOVER_PREVIEW_MAX_BYTES` (50MB — see Constants), never call the worker at all; the parent's existing `isHoverPreviewEligible` check (in `ModelList.tsx`) is extended to also fail large files, so oversized models don't even mount `HoverPreviewCanvas` on hover — the static thumbnail/icon shows instead, identical to today's non-eligible-format path.
2. On mount (for eligible, under-threshold models): render a `<canvas>` inside the existing `mountRef` div, immediately show a small spinner overlay on top of the still-visible static thumbnail, and call `startHoverPreview(canvas, model, { onReady, onError })`.
3. On `onReady` (first frame rendered): hide the spinner and static thumbnail, revealing the now-live canvas underneath (the canvas has been rendering into itself since `start`; the overlay swap is a pure CSS/visibility change, not a remount).
4. On `onError`: same fallback as today — calls the parent's `onError` prop, which clears `hoveredPreviewModelId` and reverts the card to its normal static state.
5. On unmount (unhover, hover a different card, or any other cleanup): call the `cancel()` closure returned by `startHoverPreview`.

### Constants

`HOVER_PREVIEW_MAX_BYTES = 50 * 1024 * 1024` (50MB), defined once in `frontend/components/HoverPreviewCanvas.tsx` and imported by `ModelList.tsx`'s `isHoverPreviewEligible` — a single source of truth so the parent's eligibility check and the child's own gate can never disagree.

## Fallback Behavior

If `OffscreenCanvas` or worker construction is unavailable, hover-preview shows the static thumbnail only — no live spin, and critically, no synchronous main-thread fallback. This differs deliberately from `thumbnailGenerator.ts`'s pattern (which does fall back to synchronous rendering): that background loop only ever handles one file at a time on a slow, non-interactive cadence, so a rare synchronous fallback is an acceptable degradation. Hover-preview exists purely to enhance an already-functional static-thumbnail UI, and reintroducing an unbounded synchronous parse in the one code path this design exists to eliminate would defeat the fix for exactly the environments that would hit it. Confirmed with Dave: graceful degradation to "no animation" is preferred over reintroducing freeze risk.

## Loading State

Per the existing card, the moment a hover-preview mount begins, a small spinner overlays the still-visible static thumbnail/icon; the live canvas swaps in only once the worker signals its first rendered frame (`onReady`). The card is never blank during the fetch/parse gap, regardless of how long a (sub-50MB) file takes to load.

## Cancellation & Resource Cleanup

Every path that ends a hover session — normal unhover, hovering a different card before the current one finishes loading, or component unmount — calls the same `cancel()` closure. The worker treats a `cancel` for a superseded or already-finished session as a no-op (checked via `sessionId` match), and always disposes GPU resources before acknowledging, so no sequence of rapid hover/unhover across many cards can leak WebGL contexts or leave an orphaned render loop running.

## Testing

Consistent with this project's established rule that WebGL-adjacent changes need real Playwright/headless-Chromium verification, not just type-checking:

1. **Main-thread responsiveness**: rAF-gap heartbeat test (same technique used to verify the thumbnail-worker fix and to reproduce this bug) confirms hovering a large real file no longer produces multi-second frame gaps.
2. **Visual correctness**: the live preview still renders and spins correctly for representative STL, 3MF, and STEP files under the threshold.
3. **Threshold gating**: a model just over 50MB never triggers a worker session (verified via a spy/count on worker message-posting, or by confirming no canvas/live-render mount occurs) and shows the static thumbnail; a model just under the threshold does get a live preview.
4. **Cancellation**: rapidly hovering several cards in succession leaves no orphaned worker sessions or growing WebGL context count (mirrors the context-leak regression test pattern from the thumbnail-worker plan).
5. **Error path**: a corrupt/unparseable file under the threshold still falls back to the static thumbnail via `onError`, same as today.
6. **Fallback path**: with `OffscreenCanvas` stubbed as `undefined`, hovering any eligible model shows the static thumbnail with no live preview and no thrown error.

## Out of Scope

- Any change to `thumbnailWorker.ts` / `thumbnailGenerator.ts` (background generation and upload-flow thumbnails) — already fixed, unaffected by this design.
- Any change to the detail-panel's full 3D viewer (`Viewer3D.tsx` / `STEPLoader.tsx`'s URL-based `LoadStep`) — that is a deliberate, user-initiated full view, not a passive hover, and was never implicated in this bug.
- A worker pool or multiple concurrent live hover sessions — the UI only ever shows one hover-preview at a time today, and this design preserves that.
