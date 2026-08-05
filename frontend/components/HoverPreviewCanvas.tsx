import React, { useEffect, useRef } from "react";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { LoadStepFromFile } from "./STEPLoader";
import { resolveApiOrigin } from "../services/api";
import { STLModel } from "../types";

// Disposes GPU resources (geometries/materials) belonging to a live scene's
// object graph. Needed here (unlike thumbnailGenerator.ts's one-shot
// snapshot-then-discard renderer) because a hover preview can be mounted and
// torn down repeatedly as the user moves across many cards -- without this,
// each hover/unhover cycle would leak the previous render's GPU buffers.
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

// Camera/light setup below is copied to match thumbnailGenerator.ts's
// internal `renderObjectToDataUrl` helper (Task 2's shared scene-building
// core for the static thumbnails) as closely as a continuously-rendering
// live scene allows, so the hover preview reads as "the same object, now
// spinning" rather than a subtly different render:
//   - PerspectiveCamera(45, <aspect>, 0.1, 10000) -- aspect is computed from
//     the actual card element instead of thumbnailGenerator.ts's hardcoded 1,
//     since that value assumes a fixed 300x300 offscreen canvas and this
//     mounts into a non-square card region; a hardcoded 1 here would
//     visibly stretch the model.
//   - camera.up.set(0.0, -1.0, 0.0)
//   - cameraZ = |maxDim / 2 / tan(fov/2)| * 3.5 -- the *real* settled
//     zoom-out multiplier per Task 2's report (it reconciled a genuine 3.5
//     vs 2.5 discrepancy between the old STL/3MF and STEP branches and kept
//     3.5 for every format), not the brief's illustrative 2.5.
//   - camera positioned at (center.x, center.y, cameraZ) looking at center
//     -- note the object itself is intentionally *not* recentered to the
//     origin (thumbnailGenerator.ts never does `object.position.sub(center)`
//     either); recentering here would frame the model differently than the
//     static thumbnails do.
//   - AmbientLight(0xffffff, 0.7); key DirectionalLight(0xffffff, 1.0) at
//     the camera position looking at center; back DirectionalLight(0xffffff,
//     0.5) at (-5, -5, -10).
const HoverPreviewCanvas: React.FC<{
  model: STLModel;
  onError: () => void;
}> = ({ model, onError }) => {
  const mountRef = useRef<HTMLDivElement>(null);
  // Kept in a ref (updated every render, not part of the effect's deps) so
  // that the parent passing a fresh onError closure each render -- which it
  // does, since ModelList's VirtuosoGrid itemContent is itself an inline
  // function recreated every render -- never tears down and restarts this
  // effect (and with it, the WebGL scene and rAF loop) on unrelated parent
  // re-renders. Only a genuine change of model should remount the scene.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    if (!mountRef.current) return;
    let disposed = false;
    let renderer: THREE.WebGLRenderer | null = null;
    let frameId: number | null = null;
    let liveObject: THREE.Object3D | null = null;
    let handleContextLost: ((event: Event) => void) | null = null;

    async function setup() {
      const lower = model.name.toLowerCase();
      const fileUrl = resolveApiOrigin() + model.url;

      const response = await fetch(fileUrl);
      const buffer = await response.arrayBuffer();

      let object: THREE.Object3D;
      if (lower.endsWith(".step") || lower.endsWith(".stp")) {
        // Matches thumbnailGenerator.ts's STEP branch: LoadStepFromFile
        // (ArrayBuffer-based) already returns a ready-to-render Group with
        // mesh + material built in. STEPLoader.tsx's other export, LoadStep
        // (URL-based), returns a bare BufferGeometry with no material and
        // is not an Object3D -- scene.add() on it directly would be wrong,
        // so it is deliberately not used here even though it appears in the
        // brief's illustrative sample.
        object = await LoadStepFromFile(buffer);
        // Same initial rotation thumbnailGenerator.ts's STEP branch applies,
        // in the same place (right after load, before the bounding box is
        // measured for camera framing below) -- see the STL branch's
        // comment for why this placement matters.
        object.rotation.y = 90;
        object.rotation.z = -0.3;
      } else if (lower.endsWith(".3mf")) {
        const loader = new ThreeMFLoader();
        object = loader.parse(buffer);
        // thumbnailGenerator.ts applies no initial rotation to 3MF objects.
      } else {
        const loader = new STLLoader();
        const geometry = loader.parse(buffer);
        const material = new THREE.MeshStandardMaterial({
          color: 0x3b82f6,
          roughness: 0.5,
          metalness: 0.2,
        });
        object = new THREE.Mesh(geometry, material);
        // Same initial rotation thumbnailGenerator.ts's STL branch applies.
        // Placement matters: thumbnailGenerator.ts's camera distance
        // (cameraZ = maxDim * 3.5) is derived from the world-space bounding
        // box computed AFTER this rotation, so applying it here -- before
        // the Box3().setFromObject() below -- keeps this preview's starting
        // scale/framing consistent with the static thumbnail's. Applying it
        // after framing (or not at all) would measure a different maxDim
        // and produce a visible scale "pop" the instant the live preview
        // takes over from the static thumbnail.
        object.rotation.y = 0.3;
      }

      if (disposed || !mountRef.current) {
        disposeObject3D(object);
        return;
      }
      liveObject = object;

      const box = new THREE.Box3().setFromObject(object);
      const size = box.getSize(new THREE.Vector3());
      const maxDim = Math.max(size.x, size.y, size.z);

      // A loader that "succeeds" without throwing but produces no usable
      // geometry (e.g. a corrupt/garbage file that parses into zero
      // triangles) would otherwise mount a scene that renders nothing --
      // an empty canvas sitting there for as long as the mouse stays over
      // the card, which is exactly the "visibly broken state" the fallback
      // is supposed to prevent. Route this through the same onError path
      // as a thrown exception rather than silently rendering an empty box.
      if (box.isEmpty() || !Number.isFinite(maxDim) || maxDim === 0) {
        disposeObject3D(object);
        onErrorRef.current();
        return;
      }

      const scene = new THREE.Scene();
      scene.add(object);
      const center = box.getCenter(new THREE.Vector3());

      const el = mountRef.current;
      const width = el.clientWidth || 1;
      const height = el.clientHeight || 1;

      const camera = new THREE.PerspectiveCamera(
        45,
        width / height,
        0.1,
        10000,
      );
      camera.up.set(0.0, -1.0, 0.0);
      const fov = camera.fov * (Math.PI / 180);
      let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
      cameraZ *= 3.5; // Zoom out slightly for padding -- matches thumbnailGenerator.ts
      camera.position.set(center.x, center.y, cameraZ);
      camera.lookAt(center);

      scene.add(new THREE.AmbientLight(0xffffff, 0.7));
      const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
      keyLight.position.set(
        camera.position.x,
        camera.position.y,
        camera.position.z,
      );
      keyLight.lookAt(center);
      scene.add(keyLight);

      const backLight = new THREE.DirectionalLight(0xffffff, 0.5);
      backLight.position.set(-5, -5, -10);
      scene.add(backLight);

      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setSize(width, height);
      el.appendChild(renderer.domElement);

      // A live WebGL context can be lost for reasons entirely outside this
      // component's control -- most relevantly, Fix 1's forceContextLoss()
      // not applying retroactively to contexts that were *already* evicted
      // by the browser's hard cap on simultaneous contexts before this fix
      // shipped, but also a GPU driver reset or the OS suspending the app.
      // Without this listener, a lost context leaves this card's canvas
      // permanently blank with no error and no recovery. Route it through
      // the exact same onError fallback already used for parse/geometry
      // failures, so the card falls back to the static thumbnail/icon
      // instead of staying blank forever.
      handleContextLost = (event: Event) => {
        console.error(
          `WebGL context lost for hover preview of model ${model.id}`,
        );
        if (frameId !== null) {
          cancelAnimationFrame(frameId);
          frameId = null;
        }
        if (!disposed) onErrorRef.current();
      };
      renderer.domElement.addEventListener(
        "webglcontextlost",
        handleContextLost,
      );

      function animate() {
        if (disposed || !renderer) return;
        object.rotation.y += 0.01;
        renderer.render(scene, camera);
        frameId = requestAnimationFrame(animate);
      }
      animate();
    }

    setup().catch((err) => {
      // Per this app's established convention (Task 3's thumbnail-queue
      // loop): a bad/corrupt model or format-specific parsing failure logs
      // and falls back silently rather than showing a broken hover state.
      // Calling onError() clears the parent's hoveredPreviewModelId, which
      // makes ModelList re-render this card back into its normal
      // static-thumbnail/FileBox branch.
      console.error(`Hover preview render failed for model ${model.id}:`, err);
      if (!disposed) onErrorRef.current();
    });

    return () => {
      disposed = true;
      if (frameId !== null) cancelAnimationFrame(frameId);
      if (liveObject) disposeObject3D(liveObject);
      if (renderer) {
        if (handleContextLost) {
          renderer.domElement.removeEventListener(
            "webglcontextlost",
            handleContextLost,
          );
        }
        renderer.dispose();
        // See thumbnailGenerator.ts's renderObjectToDataUrl for why this is
        // required in addition to dispose(): without it, a hover preview
        // that mounts/unmounts repeatedly as the user moves across cards
        // leaks a live WebGL context on every unmount, contributing to the
        // same context-cap eviction problem as the background thumbnail
        // loop.
        renderer.forceContextLoss();
        renderer.domElement.remove();
      }
    };
  }, [model.id, model.url, model.name]);

  return <div ref={mountRef} className="h-60 w-full" />;
};

export default HoverPreviewCanvas;
