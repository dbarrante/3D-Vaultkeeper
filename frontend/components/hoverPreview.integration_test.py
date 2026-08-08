# frontend/components/hoverPreview.integration_test.py
#
# Permanent Playwright verification for the hover-preview Web Worker
# offload (see docs/superpowers/specs/
# 2026-08-06-hover-preview-worker-offload-design.md). Exercises the real
# production code path -- real file uploads through the real backend API,
# real hover interactions against the real dev-server-built app -- rather
# than a synthetic DOM-only test, since that is how the original main-thread
# freeze bug was found in the first place.
#
# SHOULD be run against a PRODUCTION build (`bun run build` + `bun run
# preview`) for the RESPONSIVENESS assertion (Test 1's max-frame-gap < 200ms)
# to be meaningful -- `bun run dev` serves unminified, unbundled ES modules
# with Vite's dev-time transform/HMR overhead, which measured a 331ms max
# frame gap in dev vs. 34ms in a production build for the identical fixture
# and identical code -- that's expected dev-mode noise, not a regression.
#
# HISTORICAL NOTE, now resolved: an earlier version of HoverPreviewCanvas.tsx
# declared its <canvas> directly in JSX (a single persistent DOM element
# reused across every effect re-invocation), which meant React StrictMode's
# dev-only double-effect-invocation (mount -> cleanup -> mount) called
# canvas.transferControlToOffscreen() -- a one-way, permanent operation --
# TWICE on the same element, throwing "Cannot transfer control from a canvas
# for more than one time" on literally every hover in `bun run dev` (it
# failed closed, silently falling back to the static thumbnail -- see the
# Task 3 report's original Finding 2). The canvas-aspect-ratio fix (see
# HoverPreviewCanvas.tsx's effect) also creates the <canvas> element
# IMPERATIVELY inside the effect instead, so every invocation -- including
# StrictMode's second one -- gets a genuinely fresh, never-before-transferred
# element. Confirmed empirically (re-tested against `bun run dev` after that
# fix): hover preview now renders correctly in dev mode too, first hover and
# on re-hover alike, with no transfer error. Dev mode is still not the
# REQUIRED environment for this suite -- only because Test 1's responsiveness
# threshold isn't meaningful under dev's unrelated performance overhead, not
# because of any remaining canvas-transfer issue.
#
# Assumes a backend is already running:
#   cd backend && ./run.sh                      (or the equivalent uvicorn
#                                                  invocation for this OS)
# and a production build is built and served on port 4174 (NOT vite's
# default preview port, 5173 -- vite.config.ts pins preview.port to 5173,
# which in practice is frequently already occupied by an unrelated
# dev-server instance of this same app; 4174 is what this script's default
# below actually points at, so use it unless you also override
# HOVER_TEST_FRONTEND_URL to match whatever port you chose instead):
#   cd frontend && bun run build && bun run preview -- --port 4174
#
# Fixtures must exist first (see generate_fixtures.py -- the two
# *-threshold-*.stl files are gitignored and regenerated locally, not
# committed). They live in frontend/test-fixtures/ (a sibling of
# frontend/public/, NOT inside it -- see upload_fixture()'s comment for why)
# and are uploaded by reading them directly off disk, so unlike an earlier
# version of this suite, there is no need to rebuild after generating them
# -- they aren't served by the app at all, just read by this script and
# posted straight to the backend:
#   python test-fixtures/generate_fixtures.py     (from frontend/)
#
# Configurable via env vars:
#   HOVER_TEST_FRONTEND_URL  (default http://localhost:4174 -- MUST match
#                              whatever port `bun run preview` actually
#                              bound to; check its terminal output. This
#                              script only checks that the page landed on
#                              whatever URL you told it to expect -- it
#                              cannot detect "the right app, wrong port"
#                              from the inside, so a stale/wrong value here
#                              would silently test some other running
#                              instance instead of your freshly built one.)
#   HOVER_TEST_BACKEND_URL   (default http://localhost:8000)
#
# Run: python components/hoverPreview.integration_test.py
import os
import pathlib
import sys
import time
import urllib.request
from playwright.sync_api import sync_playwright

FRONTEND_URL = os.environ.get("HOVER_TEST_FRONTEND_URL", "http://localhost:4174")
BACKEND_URL = os.environ.get("HOVER_TEST_BACKEND_URL", "http://localhost:8000")
# frontend/test-fixtures/ -- a sibling of this file's own frontend/components/
# directory, not inside frontend/public/. See upload_fixture()'s comment for
# why fixtures deliberately do NOT live under public/.
FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "test-fixtures"


def set_api_override(page):
    # The app resolves its API origin via resolveApiOrigin() (frontend/
    # services/api.ts), which returns window.location.origin unless a
    # "api-port-override" key is set in localStorage (see
    # frontend/components/Settings.tsx) -- normally set once via the
    # Settings panel. Vite's dev server has no /api proxy, so without this
    # override every fetch the live app itself makes (including the hover-
    # preview worker's model-file fetch) would resolve to the frontend's
    # own origin instead of the real backend. Must run before the app's
    # first script evaluates, hence add_init_script rather than a plain
    # page.evaluate after goto.
    page.add_init_script(
        f"window.localStorage.setItem('api-port-override', '{BACKEND_URL}');"
    )


def upload_fixture(page, fixture_filename, folder_id):
    # Uses the real upload API, matching how the original bug was found --
    # against the actual production code path, not a synthetic DOM-only
    # test.
    #
    # Reads the fixture directly off disk and posts it via Playwright's OWN
    # request API (page.request.post), not a browser-side fetch(). This is
    # a whole-plan review fix: an earlier version of this function did
    # `fetch(fixturePath)` from inside the page (page.evaluate), which
    # required these fixtures to be served by URL under frontend/public/ --
    # and frontend/public/ is copied verbatim into frontend/dist/ on every
    # `bun run build`, so that requirement is exactly what caused these test
    # fixtures to risk shipping inside real release builds (desktop/
    # build.ps1 packages dist/ into the desktop app). Fixtures now live in
    # frontend/test-fixtures/ (a sibling of public/, not inside it) and are
    # uploaded from disk with no dependency on any URL or dev/preview server
    # serving them at all.
    #
    # This also happens to retire an earlier workaround: a prior version of
    # this function parsed the upload response's JSON in-browser (rather
    # than via Playwright's page.expect_response().json()) specifically to
    # avoid "Request content was evicted from inspector cache" for the
    # large 45MB/60MB fixtures -- that error came from Playwright's CDP
    # response-body buffering when reading a *browser-originated* fetch's
    # response through the inspector protocol. page.request.post() doesn't
    # go through the browser's network stack or the CDP inspector at all
    # (it's a direct HTTP request from Playwright's own Python/Node
    # process), so that failure mode doesn't apply here -- confirmed by
    # this rewrite's full test run (see the report) uploading the same
    # 45MB/60MB fixtures with no eviction error and no special-casing
    # needed.
    fixture_path = FIXTURES_DIR / fixture_filename
    file_bytes = fixture_path.read_bytes()
    response = page.request.post(
        f"{BACKEND_URL}/api/models/upload",
        multipart={
            "file": {
                "name": fixture_filename,
                "mimeType": "application/octet-stream",
                "buffer": file_bytes,
            },
            "folderId": folder_id,
        },
    )
    return response.json()


def canvas_visible(page):
    # Post canvas-sizing-fix, HoverPreviewCanvas.tsx creates the <canvas>
    # imperatively and toggles visibility via an inline
    # `canvas.style.visibility = "hidden"/"visible"` (not a Tailwind
    # 'invisible' class, which is what this helper originally checked --
    # that class check would have silently always returned true here, since
    # the imperative canvas never carries that class). Checking computed
    # style is what actually reflects the ready/not-ready state now.
    return page.evaluate(
        """
        () => {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                if (getComputedStyle(c).visibility !== 'hidden' && c.offsetHeight > 0) return true;
            }
            return false;
        }
        """
    )


def visible_canvas_count(page):
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('canvas'))
            .filter(c => getComputedStyle(c).visibility !== 'hidden' && c.offsetHeight > 0)
            .length
        """
    )


def wait_for_thumbnails_idle(page, timeout_ms=60000):
    # The background static-thumbnail generation loop (App.tsx) starts
    # working through any newly-uploaded models (including the 45MB/60MB
    # fixtures) the moment they appear -- it's already offloaded to its own
    # worker (thumbnailWorker.ts, a prior plan), but leaving it mid-flight
    # while measuring hover-preview responsiveness/heap growth would still
    # be a confound (competing worker/GPU scheduling, competing heap
    # allocity). Wait for the "Generating thumbnails" bar to disappear
    # before taking any measurement that claims to isolate the hover-preview
    # path specifically.
    try:
        page.wait_for_selector("text=Generating thumbnails", state="hidden", timeout=timeout_ms)
    except Exception:
        pass  # bar may never have appeared (already idle) -- not a failure


def delete_models(model_ids):
    for model_id in model_ids:
        if not model_id:
            continue
        req = urllib.request.Request(
            f"{BACKEND_URL}/api/models/{model_id}", method="DELETE"
        )
        try:
            urllib.request.urlopen(req)
        except Exception as err:  # best-effort cleanup, never mask a test failure
            print(f"  (cleanup warning: failed to delete model {model_id}: {err})")


def main():
    uploaded_ids = []
    console_messages = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # A generously tall viewport, not Playwright's 1280x720 default.
        # ModelList.tsx virtualizes the grid via VirtuosoGrid, which only
        # mounts DOM nodes for rows actually within (or very near) the
        # viewport -- confirmed empirically: at the default viewport, with
        # this dev library's existing ~59 pre-seeded models ahead of a
        # 4-column row, only the first ~4 newly-uploaded fixtures ever
        # mounted a DOM node at all, and get_by_text(...).hover() on the
        # 5th/6th (over/under-threshold) fixtures timed out waiting for an
        # element that was never going to appear -- not a hover-preview bug,
        # a test-viewport-too-small bug. 2000px of height comfortably fits
        # several rows so all 6 fixtures (however the dev DB happens to sort
        # them alongside whatever else is already in it) mount without
        # requiring the test to compute scroll offsets.
        page = browser.new_page(viewport={"width": 1920, "height": 2000})
        page.on("console", lambda msg: console_messages.append(msg.text))
        set_api_override(page)
        page.goto(FRONTEND_URL)
        page.wait_for_load_state("networkidle")

        # Confirm we actually landed on the intended dev-server instance
        # rather than some other process squatting on the expected port --
        # this app has been run with multiple concurrent dev-server
        # instances on adjacent ports before, and a passing run against the
        # wrong instance would be a silent false-positive.
        assert page.url.startswith(FRONTEND_URL), (
            f"expected to be on {FRONTEND_URL}, landed on {page.url} -- "
            "check HOVER_TEST_FRONTEND_URL"
        )

        folders = page.evaluate(
            "(backendUrl) => fetch(backendUrl + '/api/folders').then(r => r.json())",
            BACKEND_URL,
        )
        folder_id = folders[0]["id"]

        try:
            # Each id is appended immediately after its own upload succeeds
            # (not collected into a list at the end) so that if a LATER
            # upload in this sequence raises, the `finally` block below still
            # cleans up every upload that DID succeed -- an earlier version
            # of this script only populated the id list after all six
            # uploads succeeded, which silently leaked a model row on any
            # partial failure.
            under = upload_fixture(page, "under-threshold-45mb.stl", folder_id)
            uploaded_ids.append(under["id"])
            over = upload_fixture(page, "over-threshold-60mb.stl", folder_id)
            uploaded_ids.append(over["id"])
            small = upload_fixture(page, "small.stl", folder_id)
            uploaded_ids.append(small["id"])
            small_3mf = upload_fixture(page, "small.3mf", folder_id)
            uploaded_ids.append(small_3mf["id"])
            small_stp = upload_fixture(page, "small.stp", folder_id)
            uploaded_ids.append(small_stp["id"])
            corrupt = upload_fixture(page, "corrupt.stl", folder_id)
            uploaded_ids.append(corrupt["id"])

            # Sanity check the threshold fixtures actually straddle
            # HOVER_PREVIEW_MAX_BYTES (50MB) as intended, rather than Test 2
            # accidentally proving something about a missing/zero `size`
            # field instead of the real threshold.
            assert under["size"] < 50 * 1024 * 1024, f"under-threshold fixture is {under['size']} bytes, not under 50MB"
            assert over["size"] > 50 * 1024 * 1024, f"over-threshold fixture is {over['size']} bytes, not over 50MB"

            page.reload()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            # --- Baseline: nothing should be hovered yet, so no live canvas
            # should be visible anywhere on the page. Without this check,
            # Test 2 and Test 4 ("no visible canvas") and even Test 0's
            # first iteration could pass vacuously on a stray canvas left
            # over from page chrome, not because the hover-preview gating
            # actually did anything.
            assert not canvas_visible(page), "expected no live preview canvas before any hover"

            # Let the background thumbnail-generation loop (App.tsx) finish
            # BEFORE any hovering, not just before the responsiveness
            # measurement -- observed empirically: while it's still
            # processing the newly-uploaded fixtures (especially the
            # 45MB/60MB ones), a static thumbnail arriving for a card
            # changes that card's content and can shift VirtuosoGrid's
            # virtualization window (see ModelList.tsx's own comment on the
            # FileBox-icon-height vs thumbnail-image-height reflow problem),
            # which raced with locator lookups below and intermittently made
            # a card transiently unlocatable (or briefly double-rendered)
            # mid-run. This is a pre-existing virtualization/thumbnail-loop
            # interaction, unrelated to the hover-preview worker itself;
            # waiting it out here avoids flaking on it rather than
            # papering over it with retries.
            wait_for_thumbnails_idle(page)

            # --- Test 0: visual correctness across formats.
            # STL, STEP, and 3MF should each produce a visible, non-blank
            # live-preview canvas within a few seconds.
            #
            # 3MF was previously a KNOWN, CONFIRMED-BROKEN case: three.js's
            # ThreeMFLoader.parse() calls `new DOMParser().parseFromString(...)`
            # unconditionally (node_modules/three/examples/jsm/loaders/
            # 3MFLoader.js:215,273), and DOMParser does not exist in a
            # dedicated Worker's global scope in Chromium (confirmed
            # empirically -- typeof DOMParser === 'undefined' inside a plain
            # `new Worker(...)`). Fixed by frontend/workers/domParserShim.ts,
            # which assigns a Worker-safe DOMParser implementation
            # (xmldom-qsa) onto the Worker's global scope before
            # ThreeMFLoader is ever invoked -- see that file's comment for
            # the full root-cause writeup. The identical fix was applied to
            # thumbnailWorker.ts's 3MF branch, which shared the same bug.
            for fixture in (small, small_stp, small_3mf):
                fmt_card = page.get_by_text(fixture["name"]).first
                fmt_card.hover()
                page.wait_for_timeout(3000)
                assert canvas_visible(page), f"expected a live preview for {fixture['name']}"

                # --- Canvas aspect-ratio check (regression coverage for the
                # square-canvas-stretched-into-a-non-square-card bug caught
                # in review): the card is h-60 (240px tall) / w-full
                # (variable width) -- never square, never 600x600. Confirm
                # the live canvas's actual PIXEL BUFFER (the .width/.height
                # attributes set once, at creation, before
                # transferControlToOffscreen() -- not the CSS box, which is
                # always stretched to 100%/100% by design) now reflects the
                # card's real aspect ratio instead of a hardcoded square.
                aspect_info = page.evaluate(
                    """
                    () => {
                        const canvases = Array.from(document.querySelectorAll('canvas'))
                            .filter(c => getComputedStyle(c).visibility !== 'hidden' && c.offsetHeight > 0);
                        const c = canvases[0];
                        if (!c) return null;
                        const container = c.parentElement;
                        return {
                            canvasWidth: c.width,
                            canvasHeight: c.height,
                            containerClientWidth: container.clientWidth,
                            containerClientHeight: container.clientHeight,
                        };
                    }
                    """
                )
                assert aspect_info is not None, f"expected a locatable live canvas element for {fixture['name']}"
                assert not (aspect_info["canvasWidth"] == 600 and aspect_info["canvasHeight"] == 600), (
                    f"canvas pixel buffer is still hardcoded 600x600 for {fixture['name']} -- "
                    "the square-canvas-stretched-into-a-non-square-card regression is back"
                )
                canvas_ratio = aspect_info["canvasWidth"] / aspect_info["canvasHeight"]
                container_ratio = aspect_info["containerClientWidth"] / aspect_info["containerClientHeight"]
                assert abs(canvas_ratio - container_ratio) < 0.05, (
                    f"canvas aspect ratio {canvas_ratio:.3f} does not match container aspect ratio "
                    f"{container_ratio:.3f} for {fixture['name']} ({aspect_info}) -- render would be visibly "
                    "distorted (e.g. a sphere rendering as an ellipse) when CSS stretches the canvas to fill "
                    "its non-square box"
                )
                print(
                    f"  {fixture['name']}: canvas {aspect_info['canvasWidth']}x{aspect_info['canvasHeight']} "
                    f"(ratio {canvas_ratio:.3f}) matches container ratio {container_ratio:.3f} -- PASSED"
                )

                page.mouse.move(5, 5)
                page.wait_for_timeout(500)

            print("visual correctness: STL/STEP/3MF live preview PASSED")

            # --- Test 1: under-threshold file gets a live preview, main
            # thread stays responsive ---
            page.evaluate(
                """
                () => {
                    window.__gaps = [];
                    window.__lastT = performance.now();
                    window.__raf = function() {
                        const now = performance.now();
                        window.__gaps.push(now - window.__lastT);
                        window.__lastT = now;
                        requestAnimationFrame(window.__raf);
                    };
                    requestAnimationFrame(window.__raf);
                }
                """
            )
            card = page.get_by_text(under["name"]).first
            card.hover()
            page.wait_for_timeout(3000)
            gaps = page.evaluate("window.__gaps")
            max_gap = max(gaps) if gaps else 0
            print(f"under-threshold hover max frame gap: {max_gap:.0f}ms")
            assert max_gap < 200, f"main thread blocked {max_gap}ms hovering an under-threshold file"

            assert canvas_visible(page), "expected a visible live-preview canvas for an under-threshold file"
            page.mouse.move(5, 5)
            page.wait_for_timeout(500)

            # --- Test 2: over-threshold file never shows a live preview.
            # (The window.Worker-construction probe below is INFORMATIONAL
            # ONLY, not a real assertion -- by this point in the run the
            # hover-preview worker singleton has already been constructed by
            # Test 0/1's hovers, so `new Worker(...)` will not fire again
            # regardless of whether the over-threshold gate works. The
            # meaningful check is the canvas-visibility assertion below,
            # which is driven by ModelList.tsx's isHoverPreviewEligible size
            # gate short-circuiting BEFORE HoverPreviewCanvas ever mounts.)
            page.evaluate("window.__workerCreated = false;")
            page.evaluate(
                """
                () => {
                    const OrigWorker = window.Worker;
                    window.Worker = function(...args) {
                        if (String(args[0]).includes('hoverPreviewWorker')) window.__workerCreated = true;
                        return new OrigWorker(...args);
                    };
                }
                """
            )
            over_card = page.get_by_text(over["name"]).first
            over_card.hover()
            page.wait_for_timeout(1000)
            worker_created = page.evaluate("window.__workerCreated")
            print(f"  (informational: new Worker() constructed during over-threshold hover: {worker_created})")
            assert not canvas_visible(page), "over-threshold file should never show a live preview canvas"
            page.mouse.move(5, 5)
            page.wait_for_timeout(500)
            print("threshold gating (over-threshold never shows a live canvas): PASSED")

            # --- Test 3: cancellation across rapid hover/unhover leaves no
            # growing MAIN-THREAD heap, and -- more importantly -- does not
            # exhaust the browser's WebGL context cap.
            #
            # Caveat, stated explicitly rather than glossed over:
            # performance.memory.usedJSHeapSize reports the PAGE isolate's
            # heap only. Post-offload, the geometry buffers, three.js scene
            # graph, and WebGL context for each hover session all live in
            # the WORKER's isolate, not the page's -- so a flat/near-zero
            # page-heap delta here does NOT by itself prove the worker isn't
            # leaking. It only proves the main thread isn't accumulating
            # session state (callbacks, closures) across cycles. The context
            # cap check immediately below is what actually proves
            # `stopCurrentSession`'s forceContextLoss() is working: a leaked
            # WebGL context per cycle would exhaust Chrome's hard per-page
            # context cap within a handful of cycles, and the NEXT hover
            # after that would silently fail to render.
            has_precise_memory = page.evaluate("() => typeof performance.memory !== 'undefined'")
            mem_before = page.evaluate("performance.memory.usedJSHeapSize") if has_precise_memory else None
            for _ in range(5):
                card.hover()
                page.wait_for_timeout(600)
                page.mouse.move(5, 5)
                page.wait_for_timeout(100)
            page.wait_for_timeout(2000)
            if has_precise_memory:
                mem_after = page.evaluate("performance.memory.usedJSHeapSize")
                growth_mb = (mem_after - mem_before) / 1e6
                if mem_before == 0 and mem_after == 0:
                    print("heap growth after 5 rapid hover/unhover cycles: UNMEASURED (performance.memory returned 0 -- "
                          "Chrome quantizes/suppresses this API without --enable-precise-memory-info; not a pass, just no data)")
                else:
                    print(f"heap growth after 5 rapid hover/unhover cycles (MAIN THREAD ONLY -- see caveat above): {growth_mb:.1f}MB")
                    assert growth_mb < 100, f"heap grew {growth_mb}MB after rapid hover/unhover -- possible leak"
            else:
                print("heap growth after 5 rapid hover/unhover cycles: UNMEASURED (performance.memory unavailable in this browser)")

            # The real leak-proof check: one more hover after the 5 rapid
            # cycles must still successfully produce a live canvas. If
            # stopCurrentSession()/forceContextLoss() were failing to
            # release WebGL contexts, this is the hover that would start
            # silently failing (context creation refused once the browser's
            # cap is hit).
            card.hover()
            page.wait_for_timeout(3000)
            assert canvas_visible(page), (
                "hover after 5 rapid hover/unhover cycles produced no live canvas -- "
                "possible WebGL context leak (browser context cap exhausted)"
            )
            page.mouse.move(5, 5)
            page.wait_for_timeout(500)
            print("cancellation (no growing main-thread heap; no WebGL-context-cap exhaustion): PASSED")

            # --- Test 3b: cancel-before-restart invariant. ModelList.tsx
            # debounces hover-preview mounting by 400ms (onMouseEnter's
            # setTimeout) before HoverPreviewCanvas even mounts, so to
            # actually exercise "a session gets superseded mid-flight" (not
            # just "a hover that never started"), the first card must be
            # given enough time to mount and start its worker session, but
            # not enough time to plausibly finish loading/rendering before
            # being superseded by hovering a different card.
            first_card = page.get_by_text(under["name"]).first  # 45MB: slow enough to still be loading
            second_card = page.get_by_text(small["name"]).first  # tiny: loads almost immediately
            console_before = len(console_messages)
            first_card.hover()
            page.wait_for_timeout(600)  # past the 400ms mount debounce, but the 45MB file is still fetching/parsing
            second_card.hover()
            page.wait_for_timeout(3000)
            stale_wrong_card_fired = visible_canvas_count(page) > 1
            new_console = console_messages[console_before:]
            stale_warning_logged = any("Hover preview failed" in m for m in new_console)
            assert not stale_wrong_card_fired, "rapid hover-swap produced more than one live canvas -- stale session callback fired"
            assert not stale_warning_logged, (
                f"a '[hoverPreviewClient] Hover preview failed' warning fired during a rapid card swap -- "
                f"a superseded session's callback ran when it shouldn't have. Console output: {new_console}"
            )
            assert canvas_visible(page), "expected the second (currently-hovered) card to show a live preview"
            page.mouse.move(5, 5)
            page.wait_for_timeout(500)
            print("cancel-before-restart invariant (rapid card swap, no stale callback/canvas): PASSED")

            # --- Test 4: error path falls back to static thumbnail.
            # Uses a genuinely corrupt uploaded file rather than mocking
            # window.fetch on the main thread -- the hover-preview worker
            # performs its OWN fetch inside its own worker global scope, so
            # a main-thread fetch mock (as an earlier draft of this test
            # used, and as the original brief specified) can never intercept
            # it. corrupt.stl is 20 garbage bytes, well under the 84 bytes
            # STLLoader needs before it even attempts to read a triangle
            # count, so parsing throws synchronously and
            # hoverPreviewWorker.ts's handleStart try/catch reports a real
            # "error" message.
            corrupt_card = page.get_by_text(corrupt["name"]).first
            corrupt_card.hover()
            page.wait_for_timeout(2000)
            fell_back = not canvas_visible(page)
            assert fell_back, "corrupt file should fall back to static thumbnail, not show a broken canvas"
            page.mouse.move(5, 5)
            page.wait_for_timeout(500)
            print("error-path fallback (corrupt file never shows a broken canvas): PASSED")

            # --- Step 6: OffscreenCanvas-unsupported fallback, end-to-end.
            # window.OffscreenCanvas must be stubbed out BEFORE the app's own
            # modules first evaluate (hoverPreviewClient.ts's getWorker()
            # checks `typeof OffscreenCanvas === "undefined"` once, lazily,
            # on first use -- but stubbing after load would race with
            # whether that check already ran) -- hence a fresh browser
            # context with add_init_script, not a page.evaluate on the
            # already-loaded page above. Reuses the already-uploaded `small`
            # fixture rather than uploading a new one, since cleanup (below)
            # hasn't run yet.
            fallback_context = browser.new_context(viewport={"width": 1920, "height": 2000})
            fallback_page = fallback_context.new_page()
            fallback_page.add_init_script("window.OffscreenCanvas = undefined;")
            set_api_override(fallback_page)
            fallback_page.goto(FRONTEND_URL)
            fallback_page.wait_for_load_state("networkidle")
            fallback_page.wait_for_timeout(1500)

            fallback_card = fallback_page.get_by_text(small["name"]).first
            fallback_card.hover()
            fallback_page.wait_for_timeout(1500)
            fallback_live_canvas = canvas_visible(fallback_page)
            assert not fallback_live_canvas, "should show static thumbnail only when OffscreenCanvas is unsupported"
            fallback_context.close()
            print("OffscreenCanvas-unsupported fallback: PASSED")

            # --- Test 5: delete-on-ready regression (whole-plan review
            # fix). Directly verifies a real, previously-shipped bug:
            # hoverPreviewClient.ts's onmessage handler used to
            # unconditionally delete a session's activeCallbacks entry
            # after EITHER "ready" or "error" -- so any error arriving
            # AFTER ready (webglcontextlost, forwarded from
            # hoverPreviewWorker.ts's own contextLostHandler; or a worker
            # crash mid-animation) found the entry already gone and was
            # silently dropped at the `if (!callbacks) return;` line,
            # defeating the entire point of that forwarding logic. Uses a
            # fresh browser context so the very first hover on this page
            # deterministically gets sessionId 1 (hoverPreviewClient.ts's
            # nextSessionId is a module-scope counter starting at 1, reset
            # fresh on every page load), then captures the real constructed
            # Worker instance (same window.Worker-override technique Test 2
            # uses) so a synthetic LATE "error" for that exact session can
            # be delivered directly to hoverPreviewClient.ts's real,
            # unmodified `w.onmessage` handler -- the exact function this
            # bug lived in, not a reimplementation of it. (A real
            # webglcontextlost can't easily be triggered from outside the
            # worker's own OffscreenCanvas, so this simulates the message
            # hoverPreviewClient.ts would receive from that real event,
            # which is the only thing its onmessage handler -- the thing
            # that was actually fixed -- can observe either way.)
            regression_context = browser.new_context(viewport={"width": 1920, "height": 2000})
            regression_page = regression_context.new_page()
            regression_page.add_init_script(
                """
                window.__capturedWorker = null;
                const OrigWorker = window.Worker;
                window.Worker = function(...args) {
                    const w = new OrigWorker(...args);
                    if (String(args[0]).includes('hoverPreviewWorker')) window.__capturedWorker = w;
                    return w;
                };
                """
            )
            set_api_override(regression_page)
            regression_page.goto(FRONTEND_URL)
            regression_page.wait_for_load_state("networkidle")
            regression_page.wait_for_timeout(1500)

            regression_card = regression_page.get_by_text(small["name"]).first
            regression_card.hover()
            regression_page.wait_for_timeout(3000)
            assert canvas_visible(regression_page), (
                "expected small.stl's live preview to reach ready before the delete-on-ready regression check"
            )

            # The session that just reached ready is sessionId 1 -- the
            # very first hover-preview session ever constructed on this
            # fresh page.
            regression_page.evaluate(
                """
                () => {
                    window.__capturedWorker.onmessage({
                        data: {
                            type: 'error',
                            sessionId: 1,
                            message: 'simulated post-ready failure (delete-on-ready regression test)',
                        },
                    });
                }
                """
            )
            regression_page.wait_for_timeout(500)
            post_ready_error_reached_onerror = not canvas_visible(regression_page)
            assert post_ready_error_reached_onerror, (
                "a late 'error' message for an already-ready session did not fall back to the "
                "static thumbnail -- activeCallbacks likely deleted the session's entry on "
                "'ready', silently dropping this later error (the exact bug this test exists to catch)"
            )
            regression_context.close()
            print("delete-on-ready regression (late error after ready still reaches onError): PASSED")

        finally:
            # Step 7: delete the uploaded test-fixture model rows from the
            # dev database so repeated runs don't accumulate junk.
            delete_models(uploaded_ids)

        browser.close()
    print("ALL HOVER-PREVIEW TESTS PASSED")


if __name__ == "__main__":
    main()
