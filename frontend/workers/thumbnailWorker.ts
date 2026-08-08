import "./domParserShim";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { ThreeMFLoader } from "three/examples/jsm/loaders/3MFLoader.js";
import { loadStepGeometryFromBuffer } from "../lib/stepGeometry";
import { buildThumbnailScene } from "../lib/thumbnailScene";

type WorkerRequest =
  | { reqId: number; kind: "url"; url: string; filename: string }
  | { reqId: number; kind: "blob"; blob: Blob; filename: string };

type WorkerResponse =
  | { reqId: number; ok: true; thumbnail: string }
  | { reqId: number; ok: false; kind: "transport" | "render"; message: string };

class ThumbnailTransportErrorInWorker extends Error {}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function renderObjectToDataUrl(object: THREE.Object3D): Promise<string> {
  const { scene, camera } = buildThumbnailScene(object);

  const canvas = new OffscreenCanvas(300, 300);
  const renderer = new THREE.WebGLRenderer({
    canvas: canvas as unknown as HTMLCanvasElement,
    alpha: true,
    antialias: true,
    preserveDrawingBuffer: true,
  });
  // updateStyle defaults to true, which makes Three.js write to
  // canvas.style -- OffscreenCanvas has no .style property (it's not a DOM
  // element) and setSize() throws without this false argument.
  renderer.setSize(300, 300, false);
  renderer.render(scene, camera);

  // OffscreenCanvas has no toDataURL() (that's HTMLCanvasElement-only) --
  // convertToBlob() is its async equivalent. It must be awaited BEFORE
  // dispose()/forceContextLoss() below: unlike the old synchronous
  // toDataURL() call, convertToBlob() needs a live WebGL context to read
  // pixels from, so disposing first would produce a blank/broken image.
  const blob = await canvas.convertToBlob({ type: "image/png" });
  const dataUrl = await blobToDataUrl(blob);

  renderer.dispose();
  // dispose() only frees Three.js-side GPU resources -- it does NOT release
  // the underlying WebGL context. Without forceContextLoss(), each renderer
  // this worker creates (one per thumbnail request, for the worker's whole
  // lifetime) leaves a live context behind, and the browser's hard cap on
  // simultaneous contexts gets hit within under a minute -- the same bug
  // already found and fixed once in this codebase's pre-worker code.
  renderer.forceContextLoss();

  return dataUrl;
}

async function renderModelToDataUrl(blob: Blob, filename: string): Promise<string> {
  const contents = await blob.arrayBuffer();
  const lower = filename.toLowerCase();
  const is3MF = lower.endsWith(".3mf");
  const isSTL = lower.endsWith(".stl");
  const isSTP = lower.endsWith(".step") || lower.endsWith(".stp");

  if (!isSTL && !is3MF && !isSTP) {
    throw new Error("Unsupported file type for thumbnail");
  }

  let object: THREE.Object3D;

  if (is3MF) {
    const loader = new ThreeMFLoader();
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
    object = await loadStepGeometryFromBuffer(contents);
    object.rotation.y = 90;
    object.rotation.z = -0.3;
  }

  const dataUrl = await renderObjectToDataUrl(object);

  if (isSTL) {
    (object as THREE.Mesh).geometry.dispose();
    ((object as THREE.Mesh).material as THREE.Material).dispose();
  }

  return dataUrl;
}

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const msg = event.data;
  try {
    let blob: Blob;
    if (msg.kind === "url") {
      const response = await fetch(msg.url);
      if (!response.ok) {
        // Do NOT parse a non-ok response body as model bytes -- it's
        // JSON/HTML error content, not real file bytes.
        throw new ThumbnailTransportErrorInWorker(
          `Failed to fetch model file for thumbnail generation (${response.status} ${response.statusText}): ${msg.url}`,
        );
      }
      blob = await response.blob();
    } else {
      blob = msg.blob;
    }

    const thumbnail = await renderModelToDataUrl(blob, msg.filename);
    const response: WorkerResponse = { reqId: msg.reqId, ok: true, thumbnail };
    (self as unknown as Worker).postMessage(response);
  } catch (err) {
    const isTransport = err instanceof ThumbnailTransportErrorInWorker;
    const response: WorkerResponse = {
      reqId: msg.reqId,
      ok: false,
      kind: isTransport ? "transport" : "render",
      message: err instanceof Error ? err.message : String(err),
    };
    (self as unknown as Worker).postMessage(response);
  }
};
