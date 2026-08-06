import * as THREE from "three";
import occtimportjs from "occt-import-js";
import occtWasmUrl from "occt-import-js/dist/occt-import-js.wasm?url";
import occtWorkerUrl from "occt-import-js/dist/occt-import-js-worker.js?url";

function buildMeshFromResult(geometryMesh: any): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();

  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(geometryMesh.attributes.position.array, 3),
  );
  if (geometryMesh.attributes.normal) {
    geometry.setAttribute(
      "normal",
      new THREE.Float32BufferAttribute(geometryMesh.attributes.normal.array, 3),
    );
  }
  geometry.name = geometryMesh.name;
  const index = Uint32Array.from(geometryMesh.index.array);
  geometry.setIndex(new THREE.BufferAttribute(index, 1));

  const defaultMaterial = new THREE.MeshStandardMaterial({
    color: 0x3b82f6,
    roughness: 0.45,
    metalness: 0.1,
    side: THREE.DoubleSide,
  });

  geometry.computeBoundingSphere();
  geometry.computeVertexNormals();

  const mesh = new THREE.Mesh(geometry, defaultMaterial);
  mesh.name = geometryMesh.name;
  mesh.frustumCulled = false;
  return mesh;
}

// Deliberately framework-free (no @react-three/fiber import) so this module
// is safe to import from a Web Worker, which has no `window`/`document` and
// would risk throwing at import time if a module pulled in React-adjacent
// code. Shared by frontend/components/STEPLoader.tsx's LoadStepFromFile
// (main thread: hover preview) and frontend/workers/thumbnailWorker.ts
// (worker thread: background thumbnail generation).
export async function loadStepGeometryFromBuffer(
  fileBuffer: ArrayBuffer | Uint8Array,
): Promise<THREE.Group> {
  const initOcct = (await import("occt-import-js")).default;
  const occt = await initOcct({
    locateFile: (file: string) => {
      if (file.endsWith(".wasm")) return occtWasmUrl;
      if (file.endsWith(".worker.js")) return occtWorkerUrl;
      return file;
    },
  });

  const fileIntBuffer = new Uint8Array(fileBuffer);
  const result = occt.ReadStepFile(fileIntBuffer, null);

  const group = new THREE.Group();
  for (const resultMesh of result.meshes) {
    const mesh = buildMeshFromResult(resultMesh);
    mesh.scale.set(1.0, 1.0, 1.0);
    group.add(mesh);
  }
  return group;
}
