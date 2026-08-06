import * as THREE from "three";
import { useLoader } from "@react-three/fiber";
import occtimportjs from "occt-import-js";
import occtWasmUrl from "occt-import-js/dist/occt-import-js.wasm?url";
import occtWorkerUrl from "occt-import-js/dist/occt-import-js-worker.js?url";
import { loadStepGeometryFromBuffer } from "@/lib/stepGeometry";

export async function LoadStep(fileUrl) {
  const targetObject = new THREE.Object3D();

  const initOcct = (await import("occt-import-js")).default;
  const occt = await initOcct({
    locateFile: (file: string) => {
      if (file.endsWith(".wasm")) return occtWasmUrl;
      if (file.endsWith(".worker.js")) return occtWorkerUrl;
      return file;
    },
  });

  let response = await fetch(fileUrl);
  let buffer = await response.arrayBuffer();

  // read the imported step file
  let fileBuffer = new Uint8Array(buffer);
  let result = occt.ReadStepFile(fileBuffer);
  let geometry = new THREE.BufferGeometry();
  // process the geometries of the result
  for (let resultMesh of result.meshes) {
    geometry.setAttribute(
      "position",
      new THREE.Float32BufferAttribute(resultMesh.attributes.position.array, 3),
    );
    if (resultMesh.attributes.normal) {
      geometry.setAttribute(
        "normal",
        new THREE.Float32BufferAttribute(resultMesh.attributes.normal.array, 3),
      );
    }

    const index = Uint16Array.from(resultMesh.index.array);
    geometry.setIndex(new THREE.BufferAttribute(index, 1));
    geometry.scale(2.0, 2.0, 2.0);
    geometry.attributes.position.needsUpdate = true;
  }
  return geometry;
}

export async function LoadStepFromFile(fileBuffer) {
  return loadStepGeometryFromBuffer(fileBuffer);
}
