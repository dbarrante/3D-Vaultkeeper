# Print Organizer — Architecture (fork of STLVault)

**Base:** [moddroid94/STLVault](https://github.com/moddroid94/STLVault) — MIT licensed, cloned locally at `C:\Users\dkbar\repos\STLVault`, `origin` still points at upstream so it can be pulled from later.

**Why this base, over Printventory (the other open-source candidate from the Notion deep dive):**
- Client/server split (React+Vite frontend, FastAPI backend, SQLite) gives every new subsystem below a natural home as its own router + service module, rather than being bolted onto a single Electron main process.
- The backend can run headless as a long-lived process (Docker container, or a Windows service) independent of whether any UI window is open — required for the folder-watcher and acquisition-queue drain worker to run continuously, which is the whole point of "stability is the core requirement."
- Already ships four of the twenty-three requirements almost as-is: `#1` card/list/detail views, `#6` drag-drop folder organization, `#10` open-in-slicer (multi-slicer dropdown), `#12` (partial) URL import from Printables/Makerworld via `backend/importers/`.
- Stack (React/TS + Python/FastAPI + SQLite) matches your other projects (aether_frontend, aether_shell), so tooling and debugging habits transfer.
- MIT license, no attribution burden beyond keeping `LICENSE.md`.

Printventory's contribution here is architectural reference, not code: its AI-provider abstraction (pluggable tagging via an external AI API) is worth mirroring in `services/ai_provider.py` below, and its Docker/NAS server-mode confirms the headless-backend approach is the right one for this use case.

---

## Current state of the fork (as of clone)

```
backend/
  app.py                 # 719 lines — every route, DB access, and business logic in one file
  importers/
    makerworld.py
    printables.py
  requirements.txt       # fastapi, uvicorn, python-multipart, aiofiles, requests, starlette, pydantic
frontend/
  components/            # ModelList, DetailPanel, Sidebar, Navbar, Settings, Viewer3D, ManualModal, STEPLoader
  services/               # api.ts, thumbnailGenerator.ts
  hooks/
docker-compose.yml
```

Existing DB schema (SQLite, raw `sqlite3` module, no ORM):
```sql
CREATE TABLE folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, parentId TEXT);
CREATE TABLE models (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, folderId TEXT NOT NULL, url TEXT NOT NULL,
  size INTEGER, dateAdded INTEGER, tags TEXT, description TEXT, thumbnail TEXT, manual TEXT
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
```
No test suite exists (backend or frontend) upstream. Given "stability is the core requirement," Phase 0 below adds a test harness *before* any refactor touches behavior — characterization tests lock in what already works, then the file gets split with the tests as a safety net.

---

## Target modular architecture

```
STLVault/
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app factory, CORS, mounts every router below
│   │   ├── db.py                  # connection + schema/migrations (single source of truth)
│   │   ├── schemas.py             # pydantic request/response models
│   │   ├── routers/
│   │   │   ├── folders.py         # existing folder CRUD, extracted as-is
│   │   │   ├── models.py          # existing model CRUD + bulk ops, extracted as-is
│   │   │   ├── manuals.py         # existing manual upload/get/delete, extracted as-is
│   │   │   ├── settings.py        # existing settings + makerworld token, extracted as-is
│   │   │   ├── importers.py       # existing Printables/MakerWorld URL import, extracted as-is
│   │   │   ├── watcher.py         # NEW — #2 #3 #4 #16: configure watched folders + on-demand whole-drive scan
│   │   │   ├── inbox.py           # NEW — #9: Downloads-folder catches awaiting your file/skip decision
│   │   │   ├── ai.py              # NEW — #11 #14 #21: tag suggestions, Etsy pricing, generic AI hook
│   │   │   ├── acquisition.py     # NEW — #12 #13: extension flag intake + queue status + "what's new" feed
│   │   │   └── sync.py            # NEW — #18: Dropbox connect/status
│   │   ├── services/
│   │   │   ├── ingestion.py       # NEW — the one shared "register this file as a model" pipeline;
│   │   │   │                      #        upload endpoint, watcher, and acquisition-drain all call this
│   │   │   ├── sidecar_notes.py   # NEW — #5: pulls sibling .txt/.pdf into a model's description
│   │   │   ├── watcher_service.py # NEW — #2 #3 #4: watchdog-based scan loop, per-folder frequency
│   │   │   ├── drive_scan.py      # NEW — #16: on-demand recursive scan of arbitrary roots, same pipeline
│   │   │   ├── ai_provider.py     # NEW — thin Claude/OpenRouter client, one call site for #11/#14/#21
│   │   │   ├── etsy_pricing.py    # NEW — #21: comp lookup + AI_provider synthesis
│   │   │   ├── acquisition_worker.py # NEW — #12: drains the flagged-URL queue at a throttled, jittered rate
│   │   │   └── dropbox_sync.py    # NEW — #18
│   │   └── importers/             # moddroid94's makerworld.py / printables.py, moved under app/, unchanged
│   ├── tests/
│   │   ├── conftest.py            # NEW — temp-db + TestClient fixtures
│   │   ├── test_folders.py        # NEW — characterization tests, written before the extraction
│   │   ├── test_models.py
│   │   ├── test_manuals.py
│   │   ├── test_settings.py
│   │   ├── test_watcher.py        # NEW subsystem, TDD from scratch
│   │   ├── test_inbox.py
│   │   ├── test_ai.py
│   │   ├── test_acquisition.py
│   │   └── test_sync.py
│   └── requirements.txt           # + pytest, httpx, watchdog, pypdf, dropbox, apscheduler
├── frontend/
│   ├── components/                # existing six components, unchanged, plus:
│   │   ├── watcher/WatcherSettings.tsx      # NEW
│   │   ├── inbox/InboxPanel.tsx             # NEW
│   │   ├── ai/AiTagButton.tsx, PricingPanel.tsx  # NEW
│   │   ├── acquisition/QueuePanel.tsx, WhatsNewFeed.tsx  # NEW
│   │   └── viewer/SpinGifButton.tsx         # NEW — #15, captures frames from the existing Viewer3D canvas
│   ├── services/
│   │   ├── api.ts                 # existing, extended with new endpoint calls
│   │   └── gifEncoder.ts          # NEW — client-side GIF encode (gif.js), no new backend dependency
│   └── tests/                     # NEW — vitest + React Testing Library
├── extension/                     # NEW — separate deployable artifact (Manifest V3)
│   ├── manifest.json
│   ├── content-script.js          # injects "Flag for Print Organizer" button on model pages
│   ├── background.js              # POSTs flagged items to /api/acquisition/flag
│   └── options.html                # backend URL + per-site enable toggles
└── docs/
    ├── ARCHITECTURE.md            # this file
    └── superpowers/plans/         # bite-sized TDD plans, one per phase
```

**Why `services/ingestion.py` is the key seam:** requirement #2 (folder watch), #3 (auto-add), #9 (Downloads catch), #12 (acquisition drain), and the existing manual upload all end with the same action — "take this file, register it as a model row, generate a thumbnail." Today that logic is inlined in the `upload_model` route. Phase 0 extracts it once into `services/ingestion.py::ingest_file(path, folder_id, source) -> Model`, and every later phase calls that function instead of re-deriving it. This is the single biggest lever for both "modular" and "stability" — one code path to test and harden, four features depending on it instead of four copies.

---

## Schema evolution (SQLite, additive only — no destructive migrations)

```sql
-- Phase 0
ALTER TABLE models ADD COLUMN author TEXT;
ALTER TABLE models ADD COLUMN sourceUrl TEXT;
ALTER TABLE models ADD COLUMN category TEXT;         -- #20: curated print-type taxonomy
ALTER TABLE models ADD COLUMN colorCount INTEGER;    -- #19: multi-color print tag
ALTER TABLE models ADD COLUMN sliceSettings TEXT;    -- #23: JSON blob (nozzle/bed temp, supports, infill)

-- Phase: watcher (#2 #3 #4 #16)
CREATE TABLE watch_folders (
  id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, frequencyMinutes INTEGER NOT NULL DEFAULT 60,
  wholeDrive INTEGER NOT NULL DEFAULT 0, lastScanAt INTEGER, folderId TEXT NOT NULL
);

-- Phase: inbox (#9)
CREATE TABLE inbox_items (
  id TEXT PRIMARY KEY, path TEXT NOT NULL, detectedAt INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'   -- pending | filed | dismissed
);

-- Phase: acquisition (#12 #13)
CREATE TABLE acquisition_queue (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, site TEXT NOT NULL, title TEXT,
  flaggedAt INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',  -- pending | downloading | done | failed
  processedAt INTEGER, modelId TEXT
);
CREATE TABLE watched_sources (
  id TEXT PRIMARY KEY, site TEXT NOT NULL, creatorOrFeed TEXT NOT NULL,
  lastSeenAt INTEGER, addedAt INTEGER NOT NULL
);

-- Phase: sync (#18)
CREATE TABLE dropbox_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),   -- singleton row
  accessToken TEXT, remoteFolder TEXT, enabled INTEGER NOT NULL DEFAULT 0
);
```

Every `ALTER TABLE ADD COLUMN` and `CREATE TABLE IF NOT EXISTS` lives in `app/db.py::init_db()`, following the existing upstream pattern of wrapping `ALTER TABLE` in `try/except sqlite3.OperationalError` so re-running against an already-migrated DB is a no-op. SQLite stays the single datastore for every subsystem — no second database gets introduced.

---

## Requirement → subsystem map (all 23)

| # | Requirement | Where it lives |
|---|---|---|
| 1 | Card/list/detail views | Existing — `ModelList.tsx`, `DetailPanel.tsx` |
| 2 | Watch directories, scan on frequency | NEW — `watcher.py` + `watcher_service.py` |
| 3 | Auto-add new files | NEW — `watcher_service.py` → `ingestion.py` |
| 4 | Format-aware filtering | NEW — extension allowlist in `watcher_service.py` |
| 5 | Pull sidecar .txt/.pdf into card info | NEW — `sidecar_notes.py` |
| 6 | Drag-drop move/copy | Existing |
| 7 | Tags + notes on cards | Existing (`tags`, `description` columns) |
| 8 | Author/source/date/description/settings/notes | Existing columns + Phase 0 additions (`author`, `sourceUrl`, `sliceSettings`) |
| 9 | Catch newly-downloaded files, prompt to file | NEW — `inbox.py` + `InboxPanel.tsx` |
| 10 | Slicer integration | Existing — multi-slicer dropdown |
| 11 | OpenSCAD / OpenRouter / Claude hooks | NEW — `ai_provider.py`; OpenSCAD hook shells out to the existing `openscad-agent` project already on this machine |
| 12 | Batch download from added sites | NEW — `extension/` + `acquisition.py` + `acquisition_worker.py` |
| 13 | "What's new" feed from watched sources | NEW — `watched_sources` table + `acquisition.py` |
| 14 | AI auto-tagging + tag filters | NEW — `ai.py` (tagging) + existing tag filter UI |
| 15 | Configurable revolving GIF | NEW — `SpinGifButton.tsx` + `gifEncoder.ts` (client-side, no backend change) |
| 16 | Whole-drive consolidation | NEW — `drive_scan.py`, reuses `ingestion.py` |
| 17 | Configurable storage location | Existing — `FILE_STORAGE` env var |
| 18 | Dropbox integration | NEW — `sync.py` + `dropbox_sync.py` |
| 19 | Multi-color print tags | Phase 0 schema — `colorCount` column |
| 20 | Print type categories | Phase 0 schema — `category` column |
| 21 | AI Etsy pricing/popularity | NEW — `etsy_pricing.py`, built on `ai_provider.py` |
| 22 | Slicer recommendations | Small addition to existing slicer-dropdown logic — suggest based on `category`/file format |
| 23 | Per-card settings documentation | Existing `manual` field + Phase 0 `sliceSettings` column |

---

## Phased roadmap

| Phase | Delivers | Depends on |
|---|---|---|
| **0 — Foundation** | Test harness (pytest + vitest), `app.py` → modular package, `services/ingestion.py` extracted, Phase-0 schema columns | Nothing — do this first |
| **1 — Watcher & Inbox** | `#2 #3 #4 #9 #16` | Phase 0 (`ingestion.py`) |
| **2 — Sidecar notes + metadata** | `#5 #8 #19 #20 #22 #23` | Phase 0 |
| **3 — AI services** | `#11 #14 #21` | Phase 0 (`ai_provider.py` used by all three) |
| **4 — Spin GIF** | `#15` | None — independent, frontend-only |
| **5 — Acquisition (extension + queue)** | `#12 #13` | Phase 0 (`ingestion.py`), reuses `backend/importers/` |
| **6 — Dropbox sync** | `#18` | Phase 0 |

Phases 1–6 are independent of each other once Phase 0 lands (each only depends on Phase 0's shared pieces) — they can be built in any order or in parallel across sessions. Each phase gets its own bite-sized TDD plan under `docs/superpowers/plans/` when it's picked up, per the writing-plans scope check: bundling full TDD detail for six independent subsystems into one document isn't practical or reviewable as a single unit.

Phase 0's detailed plan is at [`docs/superpowers/plans/2026-08-02-foundation.md`](superpowers/plans/2026-08-02-foundation.md).
