// frontend/lib/hoverPreviewConstants.ts
//
// Lives outside HoverPreviewCanvas.tsx (a React component file) specifically
// so hoverPreviewWorker.ts can import it without pulling React into the
// worker bundle. Single source of truth for the hover-preview size gate --
// ModelList.tsx's isHoverPreviewEligible, HoverPreviewCanvas.tsx's own
// child-side gate, and hoverPreviewWorker.ts's byte-length guard all import
// this exact constant so none of the three can ever disagree.
//
// 50MB: comfortably covers the vast majority of real print files (this
// library's median file is 1.9MB) while excluding the rare multi-hundred-MB
// outliers that measurably froze the main thread for 11+ seconds when
// rendered synchronously (see docs/superpowers/specs/
// 2026-08-06-hover-preview-worker-offload-design.md).
export const HOVER_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;
