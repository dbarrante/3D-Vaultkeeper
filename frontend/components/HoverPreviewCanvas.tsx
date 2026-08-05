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

// Lighting is copied from thumbnailGenerator.ts's internal
// `renderObjectToDataUrl` helper (Task 2's shared scene-building core for
// the static thumbnails), so the hover preview reads as "the same object,
// now spinning" rather than a subtly different render. Camera framing is
// deliberately tighter than the static thumbnail's, since this preview has
// its own dedicated on-screen area to fill rather than a fixed 300x300
// offscreen square:
//   - PerspectiveCamera(45, <aspect>, 0.1, 10000) -- aspect is computed from
//     the actual card element (a non-square region), unlike
//     thumbnailGenerator.ts's hardcoded 1.
//   - camera.up.set(0.0, -1.0, 0.0)
//   - The object is recentered inside a wrapping pivot Group
//     (`object.position.sub(center)`, `pivot.add(object)`) so the per-frame
//     rotation below spins it around its own visual center rather than
//     around whatever arbitrary point the source file's modeling origin
//     happened to sit at (many real STL exports are not centered on their
//     own geometry, which made the object visibly orbit/swing instead of
//     spinning in place). The object's OWN rotation is frozen at its
//     initial per-format value once this is set up; only `pivot.rotation.y`
//     is animated -- rotating `object` directly here would make the fixed
//     recentering translation only valid at the one angle it was measured
//     for, causing the object to drift away from center as the animation
//     progresses.
//   - cameraZ is solved from the object's bounding SPHERE radius (not the
//     AABB's maxDim) so the object can never clip out of frame at any
//     rotation angle, with only a small 1.15x padding multiplier -- unlike
//     the static thumbnail's much larger 3.5x zoom-out (appropriate there
//     since it frames one fixed, already-final orientation with no
//     animation to protect against, and traditionally leaves more visual
//     breathing room for a small grid thumbnail). Camera and lights are
//     positioned/aimed at the world origin, since the pivot (and the
//     recentered object inside it) sits there.
//   - AmbientLight(0xffffff, 0.7); key DirectionalLight(0xffffff, 1.0) at
//     the camera position looking at the origin; back DirectionalLight
//     (0xffffff, 0.5) at (-5, -5, -10).
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

      const center = box.getCenter(new THREE.Vector3());
      // Recenter `object` within a wrapping pivot Group so it spins around
      // its own visual center instead of whatever arbitrary point the
      // source file's modeling origin happened to be at (real STL exports
      // are frequently not centered on their own geometry -- e.g.
      // positioned relative to a print-bed corner -- which made the object
      // visibly orbit/swing instead of spinning in place).
      //
      // This MUST be a separate pivot, not a fixed `object.position.sub
      // (center)` shift with `object.rotation.y` animated directly: `center`
      // is only valid for the rotation `object` had at the instant it was
      // measured (the fixed per-format initial rotation applied above, e.g.
      // 0.3 for STL). If `object`'s own rotation kept advancing every frame,
      // the fixed translation would only cancel the rotation correctly at
      // that one starting angle -- at every other angle the object visibly
      // drifts away from center, growing worse as the animation progresses
      // (confirmed empirically: the object visibly shrank into the distance
      // over a few seconds before this fix). Freezing `object`'s own
      // rotation at its initial value, recentering `object` inside `pivot`,
      // and animating `pivot.rotation.y` instead keeps the object's visual
      // center pinned to the pivot's own origin at every rotation angle.
      object.position.sub(center);
      const pivot = new THREE.Group();
      pivot.add(object);

      const scene = new THREE.Scene();
      scene.add(pivot);

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
      // Unlike the static thumbnail (thumbnailGenerator.ts, which frames a
      // single fixed orientation and uses a generous 3.5x zoom-out multiplier
      // for padding), this preview keeps rotating -- framing it that loosely
      // leaves most of the dedicated hover-preview area empty. Instead:
      // 1. Use the object's bounding SPHERE radius (not the AABB's maxDim)
      //    as the size measure. A sphere fully containing the box is
      //    rotation-invariant, so the object can never clip out of frame at
      //    ANY rotation angle -- maxDim alone only guarantees a fit at the
      //    rotation angle it was measured at.
      // 2. Solve the camera distance exactly for that sphere to fill the
      //    vertical FOV (D = R / sin(fov/2), the precise sphere-fills-frame
      //    distance), then apply only a small 1.15x padding multiplier
      //    instead of 3.5x, so the object fills most of the available space
      //    while still leaving a small margin.
      const sphere = box.getBoundingSphere(new THREE.Sphere());
      const fov = camera.fov * (Math.PI / 180);
      let cameraZ = sphere.radius / Math.sin(fov / 2);
      cameraZ *= 1.15; // small padding so the object doesn't touch the frame edge
      camera.position.set(0, 0, cameraZ);
      camera.lookAt(0, 0, 0);

      scene.add(new THREE.AmbientLight(0xffffff, 0.7));
      const keyLight = new THREE.DirectionalLight(0xffffff, 1.0);
      keyLight.position.set(
        camera.position.x,
        camera.position.y,
        camera.position.z,
      );
      keyLight.lookAt(0, 0, 0);
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
        pivot.rotation.y += 0.01;
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
