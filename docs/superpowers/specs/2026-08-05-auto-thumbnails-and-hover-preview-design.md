# Auto-Generated Thumbnails + Hover 3D Preview Design

**Status:** Approved by Dave, ready for planning.

## Problem

Today, a model only gets a thumbnail if it was added through the manual upload flow, where the browser renders the file client-side (`frontend/services/thumbnailGenerator.ts`) before the upload request is sent. Models added via a watch folder (`scan_watch_folder`) or the Import Wizard (`commit_placement_file`) both go through the same shared `ingest_file()` function, which accepts an optional `thumbnail` argument neither caller ever supplies — so those models get `thumbnail: NULL` by construction, confirmed as documented, intentional current behavior, not a bug. In Dave's real library, which relies heavily on watch folders and the Import Wizard, this means a large number of models show a generic file icon in the grid instead of a real preview.

There is no server-side 3D rendering capability anywhere in this codebase today — every existing render (thumbnail generation on upload, and the live interactive 3D viewer in the detail panel) happens in the browser via Three.js, with `occt-import-js` (a WASM OCCT build) handling STEP/STP specifically. Any fix has to either build genuinely new server-side rendering infrastructure, or find a way to reuse the browser-based rendering that already works.

Dave also wants a second, related capability: hovering over a model card in the grid should show a live, animated (auto-rotating) 3D preview of the actual model, not just the static thumbnail — reusing the same real-geometry-rendering capability the detail panel's viewer already has.

## Goals

- Every model of a supported format gets a real thumbnail, automatically:
  - **Going forward**: new models added via watch folder or Import Wizard get a thumbnail without any user action.
  - **Backfill**: existing thumbnail-less models in the library get one too, without Dave having to open each one manually.
- Both cases are handled by **one mechanism**: a background loop that runs while the desktop app is open, continuously finding models with `thumbnail IS NULL` and rendering them — there is no meaningful difference between "an old model that's never had a thumbnail" and "a model that arrived five minutes ago via a watch-folder scan." Both are just rows missing a thumbnail.
- The background loop reuses the *exact* rendering logic already proven correct for manual uploads (`thumbnailGenerator.ts`) — no new rendering code, no new dependencies, no new server-side infrastructure.
- Paced deliberately slowly, so it never competes with foreground interaction — a library of thousands finishes over hours in the background, not in one disruptive burst.
- Hovering a model card in the grid (STL/3MF/STEP only) replaces the static thumbnail, in place, with a live, slowly auto-rotating render of the real model — same size and position as the card, no popup, no layout shift. Reverts to the static thumbnail when the mouse moves away.

## Non-goals

- **No OBJ support.** OBJ is technically a supported library file type, but has no rendering path anywhere in this app today — not the live viewer, not thumbnail generation. Adding OBJ loading is out of scope here; OBJ models keep today's generic-icon fallback. A future feature can add it if it turns out to matter.
- **No new server-side rendering pipeline.** Explicitly ruled out (heavy new dependencies — a real CAD kernel for STEP files, painful to bundle into the packaged desktop `.exe` — for no benefit over reusing what already works in-browser on a single-user desktop app).
- **No "Generate All Thumbnails" button / explicit user-triggered batch run.** The background loop runs automatically and continuously whenever the app is open; no manual trigger is needed or provided by this feature.
- **No popup/overlay preview.** The hover preview replaces the card's own thumbnail in place — never a larger floating panel.
- **No thumbnail generation while the app is fully closed.** The mechanism only runs inside the running app process (the same way live rendering already only works there); it does not run as a separate always-on background service.

## Architecture

Three pieces:

1. **Backend: a way to ask for thumbnail-less models.** A small filtered query so the frontend can pull "the next model missing a thumbnail" without fetching and scanning the entire library client-side.
2. **Frontend: the background generation loop.** A hook/service, mounted once at the app root, that runs on a slow interval: ask the backend for one thumbnail-less model, fetch its real file, render it off-screen with the same code `thumbnailGenerator.ts` already uses for manual uploads, save the result via the existing model-update endpoint, wait, repeat.
3. **Frontend: hover preview.** A per-card hover handler (debounced) that, on sustained hover, fetches the model's real file and mounts a small live Three.js scene in place of the static thumbnail, auto-rotating the geometry; tears down and reverts to the static image on mouse-leave or when a different card is hovered.

Pieces 2 and 3 both end up loading and rendering a model's real file client-side — they should share the actual parsing/scene-setup logic (extracted from `thumbnailGenerator.ts` into something both the background loop and the hover preview can call) rather than duplicating it.

## Backend

**`GET /api/models/thumbnail-queue`** (or an added filter on the existing `GET /api/models` endpoint — exact shape decided at planning time): returns a small batch (e.g. one) of models where `thumbnail IS NULL`, restricted to the supported formats (STL/3MF/STEP — derived from the existing `SUPPORTED_EXTENSIONS`-style check, excluding `.obj`), and excluding any model already marked as a permanently-failed render (see Safety below). Returns just enough for the frontend to fetch the file and know which model to save the result against (`id`, `url`/download path, file extension).

No other backend changes are needed for generation — the existing `PATCH /api/models/{id}` endpoint (which already accepts a `thumbnail` field) is reused unmodified to save the result, exactly as manual upload already does today.

## Frontend: Background Generation Loop

- Mounted once, at the app root (e.g. alongside other app-lifetime effects in `App.tsx`), so it runs for the whole time the app is open regardless of which screen is showing.
- On a slow, fixed interval (a few seconds between models — exact pacing decided at planning time, tunable, but the intent is "invisible," never enough to make the UI feel sluggish): ask the backend for one thumbnail-less model, fetch its file, render it off-screen (same parsing + `THREE.WebGLRenderer({ preserveDrawingBuffer: true })` + `toDataURL` approach `thumbnailGenerator.ts` already uses), then `PATCH` the result back.
- If no thumbnail-less models remain, the loop simply finds nothing to do each tick — cheap, no special "done" state needs to be tracked separately from "the query came back empty."
- A model whose render fails (corrupt file, vanished since it was scanned, a genuine parsing error) is marked so the loop doesn't retry it forever and stall on the same broken row — the exact marking mechanism (a dedicated column vs. reusing existing state) is decided at planning time, but functionally: one real failure removes a model from the queue permanently, not just for that session.
- Runs regardless of window focus/minimization, as long as the app process itself is alive — consistent with how the app's other background work (e.g. watch-folder scanning) already behaves.

## Frontend: Hover Preview

- Attached to model cards in the grid (`frontend/components/ModelList.tsx`), gated to STL/3MF/STEP only (same format restriction as generation — a model with an unsupported/OBJ extension never attempts a hover preview, regardless of whether it happens to have a thumbnail).
- Debounced: hovering briefly and moving on (e.g. scanning across many cards) does not trigger anything. Only a sustained hover starts the fetch+render.
- On triggering: fetch the model's real file, parse and render it into a small Three.js scene sized to match the card's existing thumbnail area exactly, auto-rotating the geometry continuously.
- On mouse-leave, or when a different card starts its own hover sequence: tear down the live render and revert to the static thumbnail image immediately. Only one hover-preview scene is ever mounted at a time across the whole grid.
- If the model has no thumbnail yet (still queued in the background generator) and is hovered, the preview can still render live — hovering doesn't depend on generation having already happened for that specific model, since both pull from the same real file.

## Safety & Error Handling

- The background loop never overwrites an existing thumbnail — it only ever picks up rows where `thumbnail IS NULL`, so a manually-set or previously-generated thumbnail is never silently replaced.
- A render failure during background generation is caught per-model (matching this app's established "one bad item never blocks the rest of the batch" convention from watch-folder scanning and the Import Wizard) — one corrupt file doesn't stop the loop from continuing to the next model, and doesn't get retried indefinitely.
- Hover-preview render failures fail silently back to the static thumbnail (or the generic icon, if none exists yet) rather than showing a broken state in the grid.
- The background loop and hover preview never run heavy work concurrently competing for the same GPU/WebGL context in a way that visibly stutters the UI — pacing (the interval between background-loop models) is the primary lever for this, tuned during implementation/testing against a real large library.

## Testing

**Backend (pytest, following existing conventions):** the thumbnail-queue endpoint returns models with `thumbnail IS NULL` and excludes ones with a thumbnail already set, excludes unsupported formats (OBJ), and excludes permanently-failed rows once that marking exists.

**Frontend:** no automated test suite exists in this project (consistent with the rest of the codebase) — verified manually via the packaged build, against a real or realistic-scale library: confirm watch-folder/Import-Wizard-added models eventually get a thumbnail without any action, confirm the background loop doesn't visibly impact UI responsiveness while browsing the grid, confirm hover preview shows a live rotating render in place with no layout shift, and confirm a corrupt/deliberately-broken model file doesn't stall the background loop on that one row.
