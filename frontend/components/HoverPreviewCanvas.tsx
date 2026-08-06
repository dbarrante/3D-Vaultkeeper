// frontend/components/HoverPreviewCanvas.tsx
import React, { useEffect, useRef, useState } from "react";
import { startHoverPreview } from "../services/hoverPreviewClient";
import { resolveApiOrigin } from "../services/api";
import { STLModel } from "../types";
import CardMedia from "@mui/material/CardMedia";
import { FileBox } from "lucide-react";

// Single source of truth for the size gate -- ModelList.tsx's
// isHoverPreviewEligible imports this exact constant so the parent's
// eligibility check and this component's own gate can never disagree.
// 50MB: comfortably covers the vast majority of real print files (this
// library's median file is 1.9MB) while excluding the rare multi-hundred-MB
// outliers that measurably froze the main thread for 11+ seconds when
// rendered synchronously (see docs/superpowers/specs/
// 2026-08-06-hover-preview-worker-offload-design.md).
export const HOVER_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;

const HoverPreviewCanvas: React.FC<{
  model: STLModel;
  onError: () => void;
}> = ({ model, onError }) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  // Kept in a ref so a parent re-render passing a fresh onError closure
  // (ModelList's VirtuosoGrid itemContent is an inline function recreated
  // every render) never tears down and restarts this effect.
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    if (!mountRef.current) return;
    setReady(false);
    const el = mountRef.current;

    const canvas = document.createElement("canvas");
    // Size the transferred canvas's pixel buffer to match this card's
    // actual rendered box -- must happen here, before startHoverPreview
    // (which calls transferControlToOffscreen() internally), since a
    // transferred canvas's width/height can no longer be set from the
    // main thread afterward. Matches the pre-worker code's use of
    // clientWidth/clientHeight for camera-aspect framing -- without this,
    // the worker's fixed-square camera aspect gets stretched by CSS into
    // this card's non-square box, visibly distorting the render (spheres
    // render as ellipses).
    const width = el.clientWidth || 300;
    const height = el.clientHeight || 300;
    canvas.width = width;
    canvas.height = height;
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.visibility = "hidden";
    el.appendChild(canvas);

    const fileUrl = resolveApiOrigin() + model.url;
    const handle = startHoverPreview(
      canvas,
      { url: fileUrl, name: model.name },
      {
        onReady: () => {
          canvas.style.visibility = "visible";
          setReady(true);
        },
        onError: () => onErrorRef.current(),
      },
    );

    return () => {
      handle.cancel();
      el.removeChild(canvas);
    };
  }, [model.id, model.url, model.name]);

  return (
    <div ref={mountRef} className="h-60 w-full relative">
      {/* Static thumbnail/icon placeholder, shown until the worker's first
          frame is ready -- this component owns its own loading state rather
          than the parent showing/hiding it, since ModelList.tsx's ternary
          already hard-swaps this component in on hover (see ModelList.tsx
          around the isHoverPreviewEligible check). The live canvas itself is
          created imperatively in the effect above (not declared here in
          JSX) -- see that effect's comment for why: it needs the container's
          real clientWidth/clientHeight before transferControlToOffscreen()
          is called, and creating a fresh element per effect invocation
          means every mount (including React StrictMode's dev-only second
          invocation) gets a canvas that was never transferred before. */}
      {!ready &&
        (model.thumbnail ? (
          <CardMedia
            component="div"
            className="h-60 object-cover"
            image={model.thumbnail}
          />
        ) : (
          <div className="h-60 relative flex items-center justify-center">
            <div className="absolute inset-0 opacity-30 bg-gradient-to-tr from-blue-900/40 to-transparent" />
            <FileBox className="w-12 h-12 text-slate-600" />
          </div>
        ))}
      {!ready && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-6 h-6 rounded-full border-2 border-vault-700 border-t-blue-500 animate-spin" />
        </div>
      )}
    </div>
  );
};

export default HoverPreviewCanvas;
