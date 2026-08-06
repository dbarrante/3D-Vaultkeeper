import * as THREE from "three";

// Framework-free (pure THREE.js object construction, no canvas/renderer/DOM
// dependency) so this module is safe to import from both a Web Worker
// (frontend/workers/thumbnailWorker.ts) and the main thread (the
// synchronous fallback in frontend/services/thumbnailGenerator.ts).
export function buildThumbnailScene(
  object: THREE.Object3D,
): { scene: THREE.Scene; camera: THREE.PerspectiveCamera } {
  const scene = new THREE.Scene();
  const box = new THREE.Box3();
  box.setFromObject(object);
  scene.add(object);

  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
  camera.up.set(0.0, -1.0, 0.0);

  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());

  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = camera.fov * (Math.PI / 180);
  let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
  cameraZ *= 3.5; // Zoom out slightly for padding -- matches the pre-worker value
  camera.position.set(center.x, center.y, cameraZ);

  const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
  scene.add(ambientLight);

  const dirLight = new THREE.DirectionalLight(0xffffff, 1.0);
  dirLight.position.set(camera.position.x, camera.position.y, camera.position.z);
  dirLight.lookAt(center);
  scene.add(dirLight);

  const backLight = new THREE.DirectionalLight(0xffffff, 0.5);
  backLight.position.set(-5, -5, -10);
  scene.add(backLight);

  camera.lookAt(center);

  return { scene, camera };
}
