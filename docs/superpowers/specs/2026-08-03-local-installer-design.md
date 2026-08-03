# Local Windows Installer + License Compliance — Design

## Goal

Package 3D Vaultkeeper (currently a FastAPI backend + Vite/React frontend
run via Docker Compose or manual dev servers) as a proper, double-clickable
Windows installer with no Python/Node/Docker required on the target
machine, and make the codebase legally clean to sell — without building any
licensing/activation/copy-protection mechanism.

## Scope

Confirmed with the user:
- **License compliance only** — no license keys, trial periods, or copy
  protection. Anyone with the installer could copy it, same as most small
  commercial software.
- **Windows only** — matches the actual dev machine and everything built
  so far. Cross-platform is a future addition, not part of this design.
- **A proper Windows installer**, not a portable zip — Start Menu shortcut,
  "Add or Remove Programs" entry, clean uninstall.
- **Packaging approach: PyInstaller + FastAPI serving the frontend +
  pywebview**, chosen over an Electron or Tauri wrapper — it stays entirely
  in the existing Python stack, needs no second toolchain, and produces the
  smallest realistic install size.

## Architecture

### One process, not two

`desktop/launcher.py` is the new entry point PyInstaller bundles. It:
1. Starts uvicorn serving the existing `app.main:app` FastAPI application
   in a background thread, bound to `127.0.0.1` on a dynamically-assigned
   free port (bind to port `0`, let the OS choose — avoids colliding with
   anything else already running on the user's machine).
2. Polls a health endpoint until the server responds.
3. Opens a `pywebview` window pointed at that local URL — a native-feeling
   window (no browser chrome) backed by Windows' built-in WebView2
   runtime, not a bundled Chromium.

This collapses "backend + frontend" into a single launched process. The
Tkinter `control-app` built earlier remains a *developer* tool for the
source checkout; the installed product doesn't use or need it.

### FastAPI serves the frontend directly

`app/main.py` gains one addition, registered **after** all existing
`/api/*` routers so it only catches requests nothing else matched:

```python
app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
```

This is why no Node.js runtime needs to be bundled at all: by the time the
app ships, the frontend is `frontend/dist/` — plain static files.

### Data storage moves for the installed case

Today, `DB_PATH`/`FILE_STORAGE` (`backend/app/db.py`) default to relative
paths (`./data.db`, `./app/uploads`) — correct for a dev checkout, wrong
for an installed app: `Program Files` is typically read-only for standard
users, and a relative path is unreliable when launched from a Start Menu
shortcut.

`app/db.py`'s defaults gain one additive branch: when running as a frozen
PyInstaller build (detected via `getattr(sys, "frozen", False)`) **and**
no env var override is set, default to a per-user data directory under
`%LOCALAPPDATA%\3D Vaultkeeper\` (`data.db` and `uploads/` underneath it).
`LOCALAPPDATA` rather than `APPDATA` (Roaming) — a 3D-print library can get
large, and Roaming profiles sync across machines in domain-joined
environments, which is not something a multi-gigabyte uploads folder
should do. Dev and Docker defaults (env-var-driven) are completely
unaffected by this branch.

## Build pipeline

1. **Frontend**: `cd frontend && bun run build` → `frontend/dist/`.
2. **PyInstaller**: bundles `desktop/launcher.py`, the `app/` package, its
   dependencies, and `frontend/dist/` (as bundled data files) into a
   `--onedir` build (a folder, not a single `.exe`). `--onefile` re-unpacks
   itself to a temp directory on every launch, adding real startup
   latency; `--onedir` starts instantly and is PyInstaller's own
   recommendation beyond trivial scripts. Inno Setup packages a folder
   just as easily as a single file, so this costs nothing downstream.
3. **Inno Setup** (`desktop/installer.iss`): wraps the `--onedir` output
   into the actual `setup.exe` — installs to `Program Files\3D
   Vaultkeeper\`, Start Menu shortcut, optional Desktop shortcut checkbox,
   standard uninstaller registered in "Add or Remove Programs."
4. **`desktop/build.ps1`**: chains steps 1-3 into one repeatable "build a
   release" command.

No CI/CD automation in this design — builds are manual/on-demand, matching
a pre-launch product with no release cadence yet.

## License compliance

**`THIRD-PARTY-LICENSES.md`** at the repo root, also bundled into the
installed app (reachable from an About screen, or as a file alongside the
executable). Rather than repeating the same MIT/BSD/Apache-2.0 boilerplate
for each of the ~25 dependencies, it lists every dependency's name,
version, license type, and copyright holder in a table, plus the full
license text once per unique license type actually used (MIT, Apache-2.0,
BSD-3-Clause, ISC) — legally sufficient, far more readable than ~20
near-duplicate blocks.

**`occt-import-js` (LGPL-2.1)** gets its own dedicated paragraph, not just
a table row, since LGPL carries obligations the MIT/BSD dependencies
don't:
- Its own LICENSE text is included in full.
- Explicit note that it ships as a separate WASM/JS module rather than
  being statically compiled into the backend `.exe` — that separateness is
  what keeps it swappable/replaceable, the actual LGPL requirement.
  Implementation must verify Vite's build genuinely emits it as a distinct
  asset rather than inlining it (the expected default, but confirmed
  rather than assumed).
- The "make source available" obligation is satisfied by pointing to the
  package's own upstream repository — standard, accepted practice for an
  LGPL component whose source is already public there.

The existing MIT compliance for the `moddroid94/STLVault` base
(`LICENSE.md` preserved with the original copyright notice, README
attribution line) is unchanged — already handled prior to this design.

## Explicitly out of scope

- Licensing/activation/trial-period/copy-protection mechanism.
- Cross-platform (macOS/Linux) builds.
- Auto-update mechanism — updates are a fresh installer run for now.
- CI/CD build automation.
- **Code signing** — flagged deliberately, not silently dropped. An
  unsigned installer/exe will show Windows SmartScreen's "unrecognized
  publisher" warning on first run, a real trust hit for something people
  are asked to pay for. A code-signing certificate costs roughly
  $100-400/year and requires business/identity verification — worth
  budgeting for before an actual public launch, not blocking this first
  working build.

## Testing

No automated test suite applies to the installer itself — it's a
packaging artifact, not application logic. Verification is a manual
checklist:
- Build via `desktop/build.ps1` completes without error.
- Running `setup.exe` on a clean-ish machine state installs correctly;
  Start Menu and (if checked) Desktop shortcuts launch the app.
- The app launches with no visible console window, serves the real UI,
  and reads/writes its data under `%LOCALAPPDATA%\3D Vaultkeeper\`.
- The existing backend suite (110 tests) still passes unmodified — this
  design changes only how the app launches and where it stores data by
  default, not application logic.
- Uninstalling removes the installed program files but leaves the user's
  data directory intact — uninstall must never delete someone's library.
