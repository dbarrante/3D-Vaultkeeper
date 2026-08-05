import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { LoadStepFromFile } from "@/components/STEPLoader";

// Shared scene-building/camera/light/renderer setup, unified from the
// formerly-duplicated STL/3MF and STEP/STP inline branches. Takes a ready
// Object3D (a Mesh for STL/3MF, or the Group LoadStepFromFile returns) and
// produces the final PNG data URI. All visual parameters below are copied
// verbatim from the pre-refactor STL/3MF branch (the path most existing
// thumbnails were actually generated through), which is also the branch
// selected to resolve the one genuine numeric discrepancy between the two
// original branches: the camera-distance zoom-out multiplier (STL/3MF used
// 3.5, STEP used 2.5 — this looks like unintentional copy-paste drift given
// both branches used the identical "Zoom out slightly for padding" comment,
// so 3.5 was kept for both).
const renderObjectToDataUrl = (object: THREE.Object3D): string => {
  const scene = new THREE.Scene();
  const box = new THREE.Box3();

  box.setFromObject(object);
  scene.add(object);
  // Setup scene for snapshot

  // Transparent background

  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
  camera.up.set(0.0, -1.0, 0.0);

  // Center and scale

  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());

  // Move object to center

  // Position camera to fit object
  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = camera.fov * (Math.PI / 180);
  let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
  cameraZ *= 3.5; // Zoom out slightly for padding
  camera.position.set(center.x, center.y, cameraZ);

  // Add lights
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
  scene.add(ambientLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);

  dirLight.position.set(
    camera.position.x,
    camera.position.y,
    camera.position.z,
  );
  dirLight.lookAt(center);
  scene.add(dirLight);

  const backLight = new THREE.DirectionalLight(0xffffff, 0.5);
  backLight.position.set(-5, -5, -10);
  scene.add(backLight);

  // Render
  const renderer = new THREE.WebGLRenderer({
    alpha: true,
    antialias: true,
    preserveDrawingBuffer: true,
  });
  renderer.setSize(300, 300);
  camera.lookAt(center);
  renderer.render(scene, camera);

  const dataUrl = renderer.domElement.toDataURL("image/png");
  renderer.dispose();
  // dispose() only frees Three.js-side GPU resources (textures, programs,
  // etc.) -- it does NOT release the underlying WebGL context itself. This
  // renderer is thrown away every ~3 seconds by App.tsx's background
  // thumbnail loop for the entire lifetime of the app session; without
  // forceContextLoss(), each one leaves a live WebGL context behind and
  // Chromium's (and every browser's) hard cap on simultaneous contexts gets
  // hit within under a minute, at which point the browser evicts the OLDEST
  // context -- which can silently be the hover preview's or the detail
  // panel's, breaking them with no visible error. Must be called after
  // dispose(), not instead of it: dispose() still needs a live context to
  // release its own GPU resources.
  renderer.forceContextLoss();

  return dataUrl;
};

export const generateThumbnailFromArrayBuffer = async (
  contents: ArrayBuffer,
  filename: string,
): Promise<string> => {
  try {
    const is3MF = filename.toLowerCase().endsWith(".3mf");
    const isSTL = filename.toLowerCase().endsWith(".stl");
    const isSTP =
      filename.toLowerCase().endsWith(".step") ||
      filename.toLowerCase().endsWith(".stp");

    if (!isSTL && !is3MF && !isSTP) {
      // Skip unsupported
      throw new Error("Unsupported file type for thumbnail");
    }

    let object: THREE.Object3D;

    if (is3MF) {
      const loader = new ThreeMFLoader();
      // 3MFLoader parse returns a Group
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
      console.log("STP Detected");
      object = await LoadStepFromFile(contents);
      object.rotation.y = 90;
      object.rotation.z = -0.3;
    }

    const dataUrl = renderObjectToDataUrl(object);

    // Clean up
    if (isSTL) {
      // Dispose geometry/material created manually for STL
      (object as THREE.Mesh).geometry.dispose();
      ((object as THREE.Mesh).material as THREE.Material).dispose();
    }

    return dataUrl;
  } catch (e) {
    console.error("Error generating thumbnail", e);
    throw e;
  }
};

// Thrown by generateThumbnailFromUrl when the fetch itself didn't succeed
// (e.g. a reference-mode model's GET /api/models/{id}/download 404ing
// because its source drive is disconnected -- this app's primary ingest
// path is watch folders on external/network drives, so this is a normal,
// TRANSIENT condition, not "this file can never be rendered"). Distinct
// from a thrown parse/render error so App.tsx's background loop can tell
// "never got real file bytes" apart from "got real bytes, couldn't render
// them" and avoid permanently quarantining a model for what is really a
// transport-layer hiccup.
export class ThumbnailTransportError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ThumbnailTransportError";
  }
}

export const generateThumbnailFromUrl = async (
  url: string,
  filename: string,
): Promise<string> => {
  const response = await fetch(url);
  if (!response.ok) {
    // Do NOT attempt to parse the response body as a model file -- on a
    // non-ok response it's JSON/HTML error content, not real file bytes,
    // and feeding it to the STL/3MF/STEP parsers would throw a confusing
    // low-level parse error that looks identical to a genuinely corrupt
    // file.
    throw new ThumbnailTransportError(
      `Failed to fetch model file for thumbnail generation (${response.status} ${response.statusText}): ${url}`,
    );
  }
  const contents = await response.arrayBuffer();
  return generateThumbnailFromArrayBuffer(contents, filename);
};

export const generateThumbnail = async (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = (event) => {
      if (!event.target?.result) {
        reject(new Error("File read failed"));
        return;
      }
      const contents = event.target.result as ArrayBuffer;
      generateThumbnailFromArrayBuffer(contents, file.name)
        .then(resolve)
        .catch(reject);
    };

    reader.onerror = (e) => reject(e);
    reader.readAsArrayBuffer(file);
  });
};
