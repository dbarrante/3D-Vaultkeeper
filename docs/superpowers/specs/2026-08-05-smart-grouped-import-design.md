# Smart Grouped Import Design

**Status:** Approved by Dave, ready for planning.

**Phase note:** This is Phase 1 of a two-phase feature. Phase 2 (a browser bookmarklet + `vaultkeeper://` protocol handler that triggers this pipeline directly from a project page, without copy/pasting a URL into the app) is intentionally out of scope here and will get its own design once this phase ships — it depends on the pipeline built here already existing.

## Problem

The app already supports pasting a project URL from Printables or MakerWorld (`frontend/App.tsx`'s Import URL flow, backed by `backend/app/routers/importers.py` and `backend/app/importers/{printables,makerworld}.py`). It already discovers every file belonging to that project. But it falls short of what a multi-part print project actually needs:

- Every selected file is imported with its own `POST /api/import/importid` call, sequentially, one file at a time, each landing wherever the user manually picked in a single shared folder dropdown — there's no auto-grouping into a project-named folder.
- Only Printables and MakerWorld are supported (`importer_for_url` dispatches on a hardcoded URL substring check) — any other site's URL fails outright.
- No project-level title or description is ever captured. MakerWorld's importer reads a page title only to use as a per-file default name; Printables reads nothing at the page level. `model["description"]` is hardcoded to `f"Imported from {source_label}"`.
- Folders have no metadata at all today (`folders` table: `id, name, parentId` — nothing else), so there's nowhere to put a description even if one were captured.

For a real multi-file print (a model that ships as a body, a base, and three accessory files, say), today's flow means clicking each file in one at a time and manually creating/picking a folder — exactly the tedious manual work this feature exists to remove.

## Goals

- Pasting a project URL and confirming once results in every selected file landing together in one new folder, named after the project.
- Works for **any site**, not just Printables/MakerWorld — sites without a known API get file discovery and title/description via generic page scraping.
- The project's title and description (however discovered — structured API or scraped meta tags) are saved as real folder metadata, visible in the app.
- The existing review step (pick which files to import) is kept, but every file is pre-checked and the folder name is pre-filled — so the common case is still just one click, while an unwanted file (e.g. an alternate/optional part) can still be excluded before anything downloads.
- A batch import doesn't stall or lose everything because one file failed to download.

## Non-goals

- **No browser-trigger mechanism.** Getting the URL into the app is still manual copy/paste in this phase — that's Phase 2.
- **No official Thingiverse API integration.** Thingiverse is handled by the same generic scraper as any other unknown site, not a dedicated importer — avoids requiring Dave to register a developer app and rotate an API token every ~90 days per Thingiverse's current policy.
- **No folder-metadata editing UI beyond display.** The description is written automatically at import time; editing or clearing it later is not part of this phase.
- **No retroactive re-scan of previously-imported (pre-this-feature) folders** to backfill descriptions. Only new imports get one.

## Architecture

Three pieces:

1. **Backend: a generic-site importer**, sitting alongside the existing `PrintablesImporter`/`MakerWorldImporter`, used whenever a pasted URL doesn't match a known site. It fetches the page and returns the same shape the other two importers already return (title, description, list of files) — everything downstream (the review screen, the batch-import endpoint) works identically regardless of which importer produced the data.
2. **Backend: a batch-import endpoint** that replaces the current one-request-per-file loop. Given a set of selected files plus a folder name and description, it does the whole grouped operation server-side in one call: create (or resolve a name collision on) the destination folder, set its description, download every file, create a model row per file.
3. **Frontend: review-screen and folder-display changes.** The existing "select files to import" screen gains an editable, pre-filled project-folder-name field and pre-checks every file; submitting calls the new batch endpoint instead of looping. Folder tiles that have a description show a small info affordance to reveal it.

## Backend: Generic Scraper Importer

New module (e.g. `backend/app/importers/generic.py`), matching the existing importer interface so `importer_for_url` can dispatch to it as the fallback case (after the existing Printables/MakerWorld substring checks) instead of erroring:

- Fetches the page HTML (`requests`, already a dependency — no new HTTP client needed).
- Title/description: prefer Open Graph tags (`<meta property="og:title">` / `<meta property="og:description">`), since most maker/sharing sites populate these for link-preview purposes; fall back to `<title>` and the first `<meta name="description">` if OG tags are absent.
- Files: scan anchor (`<a href="...">`) targets for links ending in a known 3D-file extension (`.stl`, `.3mf`, `.step`, `.stp`, `.zip`) — resolved to absolute URLs against the page's own URL. Each becomes one file option, named from the link's filename.
- HTML parsing uses a proper parser (adding `beautifulsoup4` as a new backend dependency) rather than hand-rolled regex against raw HTML — regex-based HTML parsing is fragile in ways a real parser isn't, and this is exactly the kind of small, well-established, single-purpose library worth taking a dependency on rather than reinventing.
- If zero files are found, this is a clear, immediate error surfaced to the review-screen step (nothing gets created) — not a silently empty import.

## Backend: Batch Import Endpoint

New endpoint, e.g. `POST /api/import/batch`, taking the project title (used as the folder name — editable by the user before submit), description, and the list of selected file options (same shape the existing `getModelOptions` results already have). Behavior:

1. **Folder resolution.** Look for an existing folder with the given name at the root level.
   - No match: create it, set its `description`.
   - Match found: **do not import yet** — return a `409`-style "name collision" response so the frontend can ask the user reuse-vs-create-new, then resubmit with that choice included in the request.
2. **Download + create.** For each selected file, download it (reusing the existing download/storage path used by today's single-file import) and create a model row in the resolved folder, exactly as today's single-file `POST /api/import/importid` does per-file — just looped server-side in one request instead of the frontend looping client-side requests.
3. **Response.** Returns the created folder and the list of created models, plus any files that failed to download (see Error Handling) — the frontend never has to guess success/failure from silence.

**Schema change:** `folders` gains a nullable `description TEXT` column, added the same way every other schema addition in `db.py`'s `init_db()` has been made this session (a guarded `ALTER TABLE folders ADD COLUMN description TEXT`).

## Frontend: Review Screen

- The existing "select files to import" modal (`App.tsx`'s import-options modal) gets one new field: an editable, pre-filled folder-name text input, defaulted from the importer's returned title (Printables/MakerWorld's existing title-ish data, or the generic scraper's OG title).
- Every file checkbox starts checked (today it appears to start unchecked/manual — confirm current default during implementation and flip it).
- Submitting calls the new batch endpoint once, instead of the current per-file loop through `importModelFromId`.
- **Collision prompt:** if the batch endpoint reports a name collision, show a small confirm dialog — "A folder named '<name>' already exists. Add these files to it, or create a new folder?" — then resubmit with the user's choice.
- **Partial failure:** if the response includes failed files, show them plainly (e.g. a short list under a "N of M files imported" summary) rather than presenting the import as unqualified success.
- New folders always land at the library root (not inside whatever folder happens to be currently open) — consistent with how this same destination will behave in Phase 2, where there is no "currently open folder" context to inherit from at all.

## Frontend: Folder Metadata Display

- A folder tile (in the grid, where folders already render as tiles alongside models) shows a small info icon only when `folder.description` is non-empty.
- Clicking it opens a lightweight popover showing the description text. No new dedicated screen or edit capability in this phase.

## Safety & Error Handling

- A single file's download failure doesn't abort the batch — the folder is still created (or reused) with whatever files did succeed, and the failure is reported back explicitly, matching this app's established "one bad item never blocks the rest" convention (watch-folder scanning, the background thumbnail loop).
- The generic scraper finding zero files is a hard stop before any folder/model is created — there's nothing to clean up because nothing was created.
- A folder-name collision is never silently resolved either way (never silently merged, never silently forced into "(2)") — it's always a decision handed back to the user.

## Testing

**Backend (pytest, following existing conventions in `backend/tests/`):**
- Generic scraper: parses OG tags and file links correctly from a saved local HTML fixture (no live network calls in tests); falls back to `<title>`/meta-description when OG tags are absent; returns a clear error when no files are found.
- Batch endpoint: creates a new folder with description on first import; returns a collision response (not a silent merge) when the folder name already exists; a simulated single-file download failure still creates the folder and the other models, and is reported in the response.

**Frontend:** no automated test suite exists in this project (consistent with the rest of the codebase) — verified manually/via Playwright against the packaged build: paste a known multi-file Printables/MakerWorld URL and confirm one grouped folder with all files lands correctly; paste a non-Printables/MakerWorld URL (e.g. a Thingiverse thing page) and confirm the generic scraper produces a sensible title/description/file list; verify the collision prompt appears on a deliberate re-import of the same project; verify the folder-info popover shows the right description.
