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
let currentAbortController: AbortController | null = null;

function stopCurrentSession() {
  if (currentFrameId !== null) {
    cancelAnimationFrame(currentFrameId);
    currentFrameId = null;
  }
  if (currentLiveObject) {
    disposeObject3D(currentLiveObject);
    currentLiveObject = null;
  }
  if (currentAbortController) {
    currentAbortController.abort();
    currentAbortController = null;
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

  // Listen for WebGL context loss (e.g., device context swaps). The
  // pre-worker HoverPreviewCanvas.tsx had this listener; without it, a lost
  // context leaves the canvas permanently blank with no error. Since control
  // of the canvas is transferred to this worker, only the worker can listen.
  const contextLostHandler = () => {
    if (currentSessionId !== sessionId) return; // not our session
    stopCurrentSession();
    currentSessionId = null;
    const response: HoverWorkerResponse = {
      type: "error",
      sessionId,
      message: "WebGL context lost",
    };
    (self as unknown as Worker).postMessage(response);
  };
  canvas.addEventListener("webglcontextlost", contextLostHandler);

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
  currentAbortController = new AbortController();

  try {
    const response = await fetch(msg.url, {
      signal: currentAbortController.signal,
    });
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
    // Guard AbortError so cancelled sessions don't report spurious errors.
    if (err instanceof Error && err.name === "AbortError") return;
    const response: HoverWorkerResponse = {
      type: "error",
      sessionId: msg.sessionId,
      message: err instanceof Error ? err.message : String(err),
    };
    stopCurrentSession();
    currentSessionId = null;
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
