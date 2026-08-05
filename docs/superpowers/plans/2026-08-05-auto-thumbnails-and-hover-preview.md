# Auto-Generated Thumbnails + Hover 3D Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every STL/3MF/STEP model gets a real thumbnail automatically — both backfilling the existing library and covering new watch-folder/Import-Wizard arrivals — via one background loop reusing the app's existing browser-based rendering, plus a live auto-rotating hover preview in the grid.

**Architecture:** A new backend endpoint exposes "models missing a thumbnail." A frontend background loop, running for the whole app session, pulls one at a time, renders it off-screen using a shared rendering core extracted from the existing upload-time thumbnail generator, and saves the result through the existing model-update endpoint. The same shared rendering core powers a debounced hover-preview on grid cards.

**Tech Stack:** FastAPI + sqlite3 (backend), React/TypeScript + Three.js + `occt-import-js` (frontend, all rendering stays client-side).

## Global Constraints

- No OBJ support — both generation and hover preview act only on STL/3MF/STEP models.
- No new server-side rendering pipeline — all rendering stays client-side, reusing the app's existing proven Three.js/OCCT-WASM rendering.
- No "Generate All Thumbnails" button — the background loop runs automatically and continuously whenever the app is open; no manual trigger.
- No popup/overlay preview — hover preview replaces the card's own thumbnail in place, same size and position, no layout shift.
- The background loop never overwrites an existing thumbnail — it only ever acts on rows where `thumbnail IS NULL`.
- A render failure during background generation must not block the rest of the queue, and must not be retried indefinitely once marked permanently failed.
- Background loop and hover preview must never compete for GPU/WebGL resources in a way that visibly stutters the UI — pacing (a few seconds between background-loop models, never a tight loop) is the primary lever.

---

### Task 1: Backend — thumbnail-queue endpoint + failed-tracking column

**Files:**
- Modify: `backend/app/db.py` (new column, `row_to_model` update)
- Modify: `backend/app/routers/models.py` (new endpoint, extend `update_model`'s allowed fields)
- Test: `backend/tests/test_models_core.py` (existing file — append)

**Interfaces:**
- Produces: `GET /api/models/thumbnail-queue?limit=N` — returns up to `N` models (default 1) where `thumbnail IS NULL`, `thumbnailFailed` is not set, `removedAt IS NULL`, and the filename ends in `.stl`/`.3mf`/`.step`/`.stp`. Response shape matches every other model-list endpoint: a JSON array of model objects via `row_to_model()`.
- Produces: `thumbnailFailed` becomes a settable field on the existing `PATCH /api/models/{id}` endpoint — no new endpoint needed for marking failures. Later tasks call `api.updateModel(id, { thumbnailFailed: true })` to permanently remove a model from the queue after a real render failure, and `api.updateModel(id, { thumbnail: dataUrl })` (already existing) to save a successful render.

- [ ] **Step 1: Add the `thumbnailFailed` column**

In `backend/app/db.py`, find the existing migration block (a `for column, coltype in [...]:` loop containing tuples like `("author", "TEXT")`, `("filePath", "TEXT")`, etc., each followed by `cur.execute(f"ALTER TABLE models ADD COLUMN {column} {coltype}")` inside a `try/except sqlite3.OperationalError: pass`). Add one more tuple to that same list:

```python
    ("thumbnailFailed", "INTEGER"),
```

Add it as the last entry in the list (after `("filePath", "TEXT"),`), so it follows the exact same idempotent, try/except-guarded migration pattern every other column in that list already uses — no new code structure, just one more list entry.

In `row_to_model()` (also in `db.py`), find where existing optional columns are surfaced into the returned dict (e.g. a line like `"filePath": row["filePath"] if "filePath" in row.keys() else None,`). Add:

```python
        "thumbnailFailed": bool(row["thumbnailFailed"]) if "thumbnailFailed" in row.keys() and row["thumbnailFailed"] else False,
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_models_core.py` (reuse the existing `_upload(client, name=..., folder_id=...)` helper already defined near the top of that file — read it first to confirm its exact current signature before calling it):

```python
def test_thumbnail_queue_returns_models_missing_thumbnail(client):
    _upload(client, name="needs_thumb.stl")
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "needs_thumb.stl"
    assert body[0]["thumbnail"] is None


def test_thumbnail_queue_excludes_models_with_a_thumbnail(client):
    model = _upload(client, name="has_thumb.stl")
    client.patch(f"/api/models/{model['id']}", json={"thumbnail": "data:image/png;base64,fake"})
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_thumbnail_queue_excludes_permanently_failed_models(client):
    model = _upload(client, name="broken.stl")
    client.patch(f"/api/models/{model['id']}", json={"thumbnailFailed": True})
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_thumbnail_queue_excludes_unsupported_extensions(client):
    _upload(client, name="notes.pdf")
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_thumbnail_queue_respects_limit(client):
    _upload(client, name="a.stl")
    _upload(client, name="b.stl")
    _upload(client, name="c.stl")
    resp = client.get("/api/models/thumbnail-queue?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_update_model_can_set_thumbnail_failed(client):
    model = _upload(client, name="fails_to_render.stl")
    resp = client.patch(f"/api/models/{model['id']}", json={"thumbnailFailed": True})
    assert resp.status_code == 200
    assert resp.json()["thumbnailFailed"] is True
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_models_core.py -k thumbnail -v`
Expected: FAIL — `404 Not Found` on the queue endpoint (doesn't exist yet), and the `thumbnailFailed`-setting test fails because it's not in the `update_model` endpoint's allowed fields list yet.

- [ ] **Step 4: Implement**

In `backend/app/routers/models.py`, find `update_model()`'s `allowed` list (a Python list/tuple of strings like `["name", "folderId", "tags", "description", "thumbnail", "author", "sourceUrl", "category", "colorCount", "sliceSettings"]`). Add `"thumbnailFailed"` to that list.

Add a new endpoint, placed near `get_models()` (the existing `GET /api/models` endpoint) since it's a sibling list-query:

```python
@router.get("/api/models/thumbnail-queue")
def get_thumbnail_queue(limit: int = 1):
    conn = get_db_conn()
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT * FROM models
        WHERE removedAt IS NULL
          AND thumbnail IS NULL
          AND (thumbnailFailed IS NULL OR thumbnailFailed = 0)
          AND (
                LOWER(name) LIKE '%.stl'
             OR LOWER(name) LIKE '%.3mf'
             OR LOWER(name) LIKE '%.step'
             OR LOWER(name) LIKE '%.stp'
          )
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [row_to_model(r) for r in rows]
```

(The `LIKE` patterns here are hardcoded literals, not user input, so there's no wildcard-escaping concern — unlike the file-path `LIKE` avoidance elsewhere in this codebase, which was specifically about folder names that can legally contain `%`/`_`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_models_core.py -k thumbnail -v`
Expected: PASS (all 6 new tests)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && python -m pytest -v`
Expected: PASS (all tests except the one confirmed pre-existing, unrelated `test_find_sidecar_notes_reads_sibling_pdf_file` failure)

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/app/routers/models.py backend/tests/test_models_core.py
git commit -m "feat: add thumbnail-queue endpoint and thumbnailFailed tracking column"
```

---

### Task 2: Frontend — shared rendering core (extract from `thumbnailGenerator.ts`)

**Files:**
- Modify: `frontend/services/thumbnailGenerator.ts`

**Interfaces:**
- Consumes: `LoadStepFromFile` and `LoadStep` from `frontend/components/STEPLoader.tsx` (both already exist, unchanged by this task).
- Produces: `generateThumbnailFromArrayBuffer(contents: ArrayBuffer, filename: string): Promise<string>` — the format-agnostic core (loader selection by filename extension, scene/camera/lighting setup, render, `toDataURL`), usable by any caller that already has the file's bytes. Produces: `generateThumbnailFromUrl(url: string, filename: string): Promise<string>` — fetches `url`, then delegates to `generateThumbnailFromArrayBuffer`. The existing `generateThumbnail(file: File): Promise<string>` (used today by manual upload) keeps its exact current signature and behavior, but its body becomes a thin wrapper: read `file` into an `ArrayBuffer` via `FileReader` (exactly as it already does today), then call `generateThumbnailFromArrayBuffer(buffer, file.name)`. Tasks 3 and 4 both import and call `generateThumbnailFromUrl`.

This is a **structural refactor of real, already-working code** — read `frontend/services/thumbnailGenerator.ts` in full before touching it. The scene-building logic currently exists in two near-duplicate copies (one inline in the STEP/STP async branch, one in the STL/3MF branch) with slightly different camera-distance multipliers and light setup order — this task's job is to unify them into ONE shared scene-building path, not to replicate the duplication a third time. Preserve every existing visual parameter EXACTLY as currently written (camera FOV, near/far planes, light colors/intensities/positions, renderer options, output size, PNG output format) — copy these values verbatim from the current file rather than re-deriving them from memory, since a subtly wrong camera distance or light intensity would silently produce worse-looking thumbnails than today's manual-upload path without causing any test failure or error.

Concretely:

- [ ] **Step 1: Read the current file in full**

Read `frontend/services/thumbnailGenerator.ts` completely. Identify: (a) the `FileReader`/`file.arrayBuffer()` step that is the ONLY part requiring a browser `File` object — everything after the raw bytes are obtained is format-agnostic; (b) the STL/3MF branch's loader selection and scene/camera/light/renderer setup; (c) the STEP/STP branch's equivalent setup (inline inside an async IIFE calling `LoadStepFromFile`); (d) exactly how the two branches currently differ (e.g. camera distance multiplier, `camera.up` set order) — note these differences don't need to be preserved as differences if they were unintentional copy-paste drift, but confirm this is genuinely accidental drift and not a deliberate per-format tuning choice before unifying (if in doubt, keep whichever value the STL/3MF branch uses, since that's the path most users' thumbnails have actually been generated through so far).

- [ ] **Step 2: Extract a shared scene-building helper**

Write a new internal (not exported) function that takes a ready `THREE.Object3D` (a `Mesh` for STL/3MF after wrapping a parsed `BufferGeometry` in a `Mesh`+`Material`, or the `Group` `LoadStepFromFile` already returns directly) and produces the final PNG data URI — this is the unification point for the duplicated camera/light/renderer setup you identified in Step 1. Both format branches call this same helper after producing their respective `Object3D`.

- [ ] **Step 3: Add `generateThumbnailFromArrayBuffer`**

```typescript
export const generateThumbnailFromArrayBuffer = async (
  contents: ArrayBuffer,
  filename: string,
): Promise<string> => {
  // Body: identical format-detection-by-filename-extension and loader
  // selection the current generateThumbnail already does after its
  // FileReader completes, calling the Step 2 shared scene-building helper
  // at the end of each branch instead of each branch's own inline setup.
};
```

- [ ] **Step 4: Add `generateThumbnailFromUrl`**

```typescript
export const generateThumbnailFromUrl = async (
  url: string,
  filename: string,
): Promise<string> => {
  const response = await fetch(url);
  const contents = await response.arrayBuffer();
  return generateThumbnailFromArrayBuffer(contents, filename);
};
```

- [ ] **Step 5: Rewrite `generateThumbnail` as a thin wrapper**

The exported `generateThumbnail(file: File): Promise<string>` keeps its exact current signature (so `frontend/App.tsx`'s existing call site at the manual-upload flow needs zero changes). Its body becomes: read `file` via `FileReader`/`file.arrayBuffer()` exactly as today, then `return generateThumbnailFromArrayBuffer(buffer, file.name);`.

- [ ] **Step 6: Verify the refactor produces real, correct renders — not just "compiles"**

No frontend automated test suite exists in this project, and "the code compiles and doesn't throw" is not evidence that 3D rendering actually produced a correct picture. Verify with an actual browser rendering pass, using Playwright (available in this environment) against Chromium, which has real WebGL support:

1. Start the frontend dev server (`cd frontend && bun run dev`, or however this project's dev server is normally started — check `frontend/package.json`'s `scripts` if unsure).
2. Write a small standalone Python script (using `playwright.sync_api`) that: launches headless Chromium, navigates to the running dev server, and uses `page.evaluate()` to import and call `generateThumbnailFromArrayBuffer` directly against real test fixture bytes for at least one STL file, one 3MF file, and one STEP file (small real geometry files — a simple cube or similar is fine; this repo's `backend/tests/` fixtures directory or existing test STL byte literals used elsewhere this session are a reasonable source, or construct a minimal valid STL/3MF/STEP by hand if none exist).
3. For each result, assert three things, not just "no exception was thrown": (a) the returned string starts with `data:image/png;base64,`; (b) decoding the base64 payload and loading it produces a 300×300 image (matching the existing fixed output size); (c) the image is NOT blank/uniform — sample multiple pixels across the image (e.g. via drawing the decoded image to an offscreen `<canvas>` inside the same `page.evaluate()` call and reading `getImageData` at several coordinates) and confirm they aren't all identical, which would indicate a blank/failed render rather than an actual rendered model.
4. Save at least one of the resulting PNGs to disk (e.g. via a screenshot or by writing the decoded base64 bytes to a file) and visually confirm it actually looks like a rendered 3D object, not noise or a blank frame — do this for real, don't just claim it.

Report the exact commands run and what was visually confirmed in your task report — this is the one task in this plan where "I wrote code that should work" is explicitly not an acceptable substitute for "I generated a real image and looked at it."

- [ ] **Step 7: Commit**

```bash
git add frontend/services/thumbnailGenerator.ts
git commit -m "refactor: extract shared thumbnail rendering core, add URL-based generation"
```

---

### Task 3: Frontend — background generation loop

**Files:**
- Modify: `frontend/services/api.ts`
- Modify: `frontend/App.tsx`

**Interfaces:**
- Consumes: `GET /api/models/thumbnail-queue` (Task 1), `generateThumbnailFromUrl` (Task 2), the existing `api.updateModel` (unchanged).
- Produces: nothing consumed by Task 4 — Task 4 uses `generateThumbnailFromUrl` directly, independent of this task's polling loop.

This task has no backend changes and no automated test cycle — verification is manual via the packaged build, per this project's established convention. However, the loop's actual effect (models gaining real thumbnails over time) IS observable and must be manually confirmed, not just assumed from the code reading correct.

- [ ] **Step 1: Add the queue-fetch API wrapper**

Add to the exported `api` object in `frontend/services/api.ts`, matching the existing `updateModel` wrapper's style (arrow function, `fetch` against `` `${getApiBaseUrl()}/...` ``, throw on `!res.ok`, `return res.json()`):

```typescript
  getThumbnailQueue: async (limit: number = 1): Promise<STLModel[]> => {
    const res = await fetch(`${getApiBaseUrl()}/models/thumbnail-queue?limit=${limit}`);
    if (!res.ok) throw new Error("Failed to fetch thumbnail queue");
    return res.json();
  },
```

- [ ] **Step 2: Add the background loop effect to `App.tsx`**

Import `generateThumbnailFromUrl` from `frontend/services/thumbnailGenerator.ts` and `resolveApiOrigin` (the same function `Viewer3D.tsx` uses to build a fetchable URL from a model's backend-relative `url` field — check `frontend/services/api.ts`'s exports for its exact current name and import path, since this file already exports it and `Viewer3D.tsx` already imports it under an alias).

Add a new `useEffect` in `App.tsx`, as a sibling to the existing initial-data-fetch effect (the one that calls `fetchData()` once on mount):

```tsx
  useEffect(() => {
    let cancelled = false;

    async function tick() {
      if (cancelled) return;
      try {
        const queue = await api.getThumbnailQueue(1);
        if (queue.length > 0 && !cancelled) {
          const model = queue[0];
          const fileUrl = resolveApiOrigin() + model.url;
          try {
            const thumbnail = await generateThumbnailFromUrl(fileUrl, model.name);
            if (!cancelled) {
              await api.updateModel(model.id, { thumbnail });
            }
          } catch (renderErr) {
            console.error(`Thumbnail generation failed for model ${model.id}:`, renderErr);
            if (!cancelled) {
              await api.updateModel(model.id, { thumbnailFailed: true }).catch(() => {});
            }
          }
        }
      } catch (queueErr) {
        console.error("Thumbnail queue fetch failed:", queueErr);
      }
      if (!cancelled) {
        setTimeout(tick, 3000);
      }
    }

    const startTimer = setTimeout(tick, 3000);

    return () => {
      cancelled = true;
      clearTimeout(startTimer);
    };
  }, []);
```

(A self-rescheduling `setTimeout` chain rather than `setInterval` is used deliberately: it guarantees the next tick never starts until the current render+save has fully finished, so a slow STEP render can never overlap with the next queue fetch — `setInterval` would not give this guarantee. The `cancelled` flag mirrors this app's existing pattern for cleanup-safe async effects. The 3-second delay between ticks is the "genuinely slow, never a tight loop" pacing the plan's Global Constraints require — this app-wide effect runs for the entire session regardless of which screen is showing, since it's mounted at `App.tsx`'s top level, not inside any specific view.)

- [ ] **Step 3: Manual verification**

Rebuild, uninstall, reinstall, hash-verify per this project's established convention. Against a test library containing several models with `thumbnail: NULL` (both copy-mode and reference-mode, at least one of each of STL/3MF/STEP, plus one deliberately-corrupt file to test the failure path):

- Leave the app open and idle for a few minutes; confirm thumbnail-less models progressively gain real thumbnails in the grid without any user action, a few seconds apart, without the UI ever feeling sluggish while this happens.
- Confirm a model that already has a thumbnail is never touched (its thumbnail doesn't change/flicker).
- Confirm the deliberately-corrupt file gets marked failed (check via `GET /api/models/thumbnail-queue` no longer including it, or by inspecting the DB directly) and the loop continues on to other models rather than stalling.
- Confirm a fresh watch-folder scan or Import Wizard commit that adds new thumbnail-less models results in those new models also eventually getting thumbnails, with no separate code path needed — they're just more rows in the same queue.

- [ ] **Step 4: Commit**

```bash
git add frontend/services/api.ts frontend/App.tsx
git commit -m "feat: add background thumbnail generation loop"
```

---

### Task 4: Frontend — hover preview

**Files:**
- Modify: `frontend/components/ModelList.tsx`

**Interfaces:**
- Consumes: `generateThumbnailFromUrl` is NOT used here directly — hover preview needs a *live, interactive, continuously-rendering* view (auto-rotating), not a single static snapshot, so it reuses the loading/parsing half of Task 2's work conceptually but mounts a persistent Three.js scene rather than calling the snapshot-and-return function. Reuses the same URL-construction pattern (`resolveApiOrigin() + model.url`) Task 3 uses.

- [ ] **Step 1: Add hover state and format gating**

In `frontend/components/ModelList.tsx`, add a new piece of state tracking which model (if any) is currently showing a live hover preview:

```tsx
  const [hoveredPreviewModelId, setHoveredPreviewModelId] = useState<string | null>(null);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
```

Add a helper to determine whether a model is eligible for hover preview (same STL/3MF/STEP restriction as Task 1/3, derived from the model's `name`):

```tsx
  const isHoverPreviewEligible = (model: STLModel): boolean => {
    const lower = model.name.toLowerCase();
    return lower.endsWith(".stl") || lower.endsWith(".3mf") || lower.endsWith(".step") || lower.endsWith(".stp");
  };
```

- [ ] **Step 2: Wire debounced hover handlers onto the existing card element**

Find the model card's outer `<div>` (already has `draggable`, `onDragStart`, `onContextMenu`, `onClick` — no existing `onMouseEnter`/`onMouseLeave`). Add:

```tsx
  onMouseEnter={() => {
    if (!isHoverPreviewEligible(model)) return;
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = setTimeout(() => setHoveredPreviewModelId(model.id), 400);
  }}
  onMouseLeave={() => {
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = null;
    setHoveredPreviewModelId((current) => (current === model.id ? null : current));
  }}
```

(400ms debounce: a sustained hover triggers the preview, but quickly scanning across many cards while moving the mouse does not. Clearing the timer on mouse-leave before it fires means a quick pass-through never starts a render at all. Since `hoveredPreviewModelId` is a single piece of state — not per-card — only one card's preview can ever be active at a time across the whole grid, satisfying the "only one live scene mounted at once" requirement automatically: starting a new hover on a different card while one is already active immediately replaces the state value, tearing down the previous card's live view as a natural consequence of React re-rendering that card back to its static thumbnail.)

- [ ] **Step 3: Render the live preview in place of the static thumbnail**

Find the existing `{model.thumbnail ? (<CardMedia .../>) : (<>...FileBox fallback...</>)}` block. Change it to a three-way branch:

```tsx
{hoveredPreviewModelId === model.id && isHoverPreviewEligible(model) ? (
  <HoverPreviewCanvas model={model} />
) : model.thumbnail ? (
  <CardMedia
    component="div"
    className="h-60 object-cover"
    image={model.thumbnail}
  />
) : (
  <>
    <div className="absolute inset-0 opacity-30 group-hover:opacity-50 transition-opacity bg-gradient-to-tr from-blue-900/40 to-transparent" />
    <FileBox className="w-12 h-12 text-slate-600 group-hover:text-blue-400 transition-colors" />
  </>
)}
```

- [ ] **Step 4: Implement `HoverPreviewCanvas`**

Add a new small component in the same file (or a new sibling file `frontend/components/HoverPreviewCanvas.tsx` if you judge `ModelList.tsx` is already large enough that adding this inline would hurt readability — your call, but keep it colocated/easy to find either way):

```tsx
const HoverPreviewCanvas: React.FC<{ model: STLModel }> = ({ model }) => {
  const mountRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!mountRef.current) return;
    let disposed = false;
    let renderer: THREE.WebGLRenderer | null = null;
    let frameId: number | null = null;

    async function setup() {
      const lower = model.name.toLowerCase();
      const fileUrl = resolveApiOrigin() + model.url;
      let object: THREE.Object3D;

      if (lower.endsWith(".step") || lower.endsWith(".stp")) {
        object = await LoadStep(fileUrl);
      } else {
        const response = await fetch(fileUrl);
        const buffer = await response.arrayBuffer();
        const Loader = lower.endsWith(".3mf") ? ThreeMFLoader : STLLoader;
        const geometry = new (Loader as any)().parse(buffer);
        const material = new THREE.MeshStandardMaterial({ color: 0x3b82f6, roughness: 0.45, metalness: 0.1 });
        object = new THREE.Mesh(geometry, material);
      }

      if (disposed || !mountRef.current) return;

      const scene = new THREE.Scene();
      scene.add(object);
      const box = new THREE.Box3().setFromObject(object);
      const center = box.getCenter(new THREE.Vector3());
      object.position.sub(center);
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z) || 1;

      const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
      const cameraZ = maxDim * 2.5;
      camera.position.set(cameraZ, cameraZ, cameraZ);
      camera.lookAt(0, 0, 0);

      scene.add(new THREE.AmbientLight(0xffffff, 0.7));
      const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
      keyLight.position.copy(camera.position);
      scene.add(keyLight);

      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      const el = mountRef.current!;
      renderer.setSize(el.clientWidth, el.clientHeight);
      el.appendChild(renderer.domElement);

      function animate() {
        if (disposed) return;
        object.rotation.y += 0.01;
        renderer!.render(scene, camera);
        frameId = requestAnimationFrame(animate);
      }
      animate();
    }

    setup().catch((err) => console.error("Hover preview render failed:", err));

    return () => {
      disposed = true;
      if (frameId !== null) cancelAnimationFrame(frameId);
      if (renderer) {
        renderer.dispose();
        renderer.domElement.remove();
      }
    };
  }, [model.id, model.url, model.name]);

  return <div ref={mountRef} className="h-60 w-full" />;
};
```

Match the camera/lighting constants here to whatever Task 2's shared scene-building helper actually ended up using (read that code before writing this, so the hover preview looks visually consistent with the static thumbnails rather than subtly different) — the values shown above are illustrative, not to be copied blindly if Task 2 landed on different exact numbers.

You'll need to import `LoadStep` from `frontend/components/STEPLoader.tsx`, `STLLoader`/`ThreeMFLoader` from the same `three/examples/jsm/loaders/...` paths `thumbnailGenerator.ts` already imports them from, `THREE` from `"three"`, and `resolveApiOrigin` from `frontend/services/api.ts` — check each import path against how the existing files already import them, since this project has had import-path/naming inconsistencies flagged before this session (e.g. two different same-named `getApiBaseUrl` helpers across files).

- [ ] **Step 5: Verify the hover preview actually renders — same rigor as Task 2**

Using Playwright against Chromium (real WebGL support), with the packaged or dev build running against a test library containing at least one real STL, one 3MF, and one STEP model:

1. Navigate to the grid, locate a model card.
2. Move the mouse over it and hold (Playwright's `locator.hover()`), then wait at least 500ms (past the 400ms debounce).
3. Take a screenshot of just that card's region at two different points in time roughly 500ms apart, and confirm the two screenshots are NOT pixel-identical — this is your evidence that something is actually animating (auto-rotating), not just a static image sitting there.
4. Move the mouse away (`locator2.hover()` on a different element, or move off the grid entirely) and confirm the card reverts to showing the static thumbnail (or the `FileBox` fallback if that model has none yet) — take a screenshot and confirm it matches what the card looked like before hovering.
5. Do this for at least one file of each of the three supported formats, since STEP goes through a completely different loading code path (`LoadStep`, OCCT WASM) than STL/3MF (`STLLoader`/`ThreeMFLoader`), and a bug in one path would not be caught by testing only the other.

Report the exact commands/script used and what was visually confirmed — as with Task 2, do not report this task done based on the code merely compiling.

- [ ] **Step 6: Commit**

```bash
git add frontend/components/ModelList.tsx
git commit -m "feat: add live auto-rotating hover preview to model grid cards"
```

---

## Self-Review Notes

- **Spec coverage:** every Goals-section item maps to a task — going-forward + backfill via one mechanism (Tasks 1 and 3, no separate code path for either case, confirmed by Task 3's manual verification explicitly checking both), reused rendering logic with no new dependencies (Task 2's extraction, reused unmodified by Task 3 and conceptually by Task 4), slow pacing (Task 3's 3-second self-rescheduling loop), in-place hover preview with no layout shift (Task 4, same card element, same size).
- **Placeholder scan:** the one deliberate exception is Task 2/Task 4's explicit instruction to copy exact numeric constants from the real current file rather than trust numbers reproduced here from memory — this is a stated precision safeguard, not a vague "add appropriate values" placeholder; Task 4's illustrative camera/light constants are explicitly flagged as illustrative-only with an explicit instruction on what to do instead (match Task 2's real output).
- **Type consistency:** `generateThumbnailFromUrl(url: string, filename: string): Promise<string>` (Task 2) is called identically in Task 3's `App.tsx` effect. `api.getThumbnailQueue(limit): Promise<STLModel[]>` (Task 3) matches the array-of-`row_to_model()`-dicts shape Task 1's backend endpoint actually returns. `thumbnailFailed` is added to both `row_to_model()`'s output (Task 1) and is used identically as a boolean-ish PATCH field by Task 3 (`{ thumbnailFailed: true }`) — matches the `update_model` allowed-fields addition from Task 1.
