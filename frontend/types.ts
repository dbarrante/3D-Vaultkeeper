// Vite environment variables type declaration
declare global {
  interface ImportMetaEnv {
    readonly VITE_APP_TAG: string;
    readonly VITE_API_URL: string;
  }

  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }
}

export interface Folder {
  id: string;
  name: string;
  parentId: string | null;
  icon?: string;
}

export interface STLModel {
  id: string;
  name: string;
  folderId: string;
  url: string; // Blob URL
  size: number;
  dateAdded: number;
  tags: string[];
  description: string;
  dimensions?: { x: number; y: number; z: number };
  thumbnail?: string;
  manual?: string | null;
  author?: string | null;
  sourceUrl?: string | null;
  category?: string | null;
  colorCount?: number | null;
  sliceSettings?: string | null;
  storageMode?: "copy" | "reference";
  missing?: boolean;
  sourcePath?: string | null;
  filePath?: string | null;
}

export interface STLModelCollection {
  source?: string;
  parentId: string;
  id: string;
  name: string;
  folder: string | null;
  previewPath: string;
  typeName: string;
}

export interface StorageStats {
  used: number;
  total: number;
}

export interface WatchFolder {
  id: string;
  path: string;
  folderId: string;
  frequencyMinutes: number;
  lastScanAt: number | null;
  enabled: boolean;
}

export interface InboxItem {
  id: string;
  path: string;
  detectedAt: number;
  status: string;
}

export enum ViewMode {
  GRID = "GRID",
  LIST = "LIST",
}

export interface ImportTreeNode {
  name: string;
  path: string;
  folders: ImportTreeNode[];
  files: { name: string; path: string; isModel: boolean; size: number }[];
}

// Sentinel folder id for File-mode's synthetic bucket holding models whose
// filePath has no real subdirectory structure to group by (pre-feature flat
// copy-mode uploads). Shared between Sidebar.tsx (Task 9, builds this node)
// and App.tsx (Task 10, filters by it) so the two never drift apart.
export const FILE_VIEW_UPLOADS_BUCKET_ID = "__uploads__";

/**
 * Splits a model's filePath into the "meaningful" path segments used by
 * File-mode grouping (Sidebar.tsx) and filtering (App.tsx) -- the single
 * definition both consume, so they can never derive different node ids
 * for the same file. Normalizes backslashes, drops the filename itself,
 * and collapses everything up through a literal "uploads" directory
 * (the flat pre-feature copy-mode storage location) into nothing, so a
 * model stored flat there has zero meaningful segments and lands in the
 * synthetic Uploads bucket instead of showing a raw absolute path. A
 * path with no "uploads" segment at all (e.g. a watch-folder's real
 * absolute location) keeps its full segment chain, drive letter
 * included, since that IS the meaningful location for that file.
 */
export function fileViewSegments(filePath: string): string[] {
  const normalized = filePath.replace(/\\/g, "/");
  const segments = normalized.split("/").filter((s) => s.length > 0);
  segments.pop(); // drop the filename
  const uploadDirIndex = segments.findIndex((s) => s.toLowerCase() === "uploads");
  return uploadDirIndex >= 0 ? segments.slice(uploadDirIndex + 1) : segments;
}

export interface ImportPlacement {
  sourcePath: string;
  isFolder: boolean;
  targetFolderId: string;
}

export interface ImportResult {
  sourcePath: string;
  placementSourcePath: string;
  status: "ok" | "error";
  error?: string;
  isModel: boolean;
}

export type AppState = {
  folders: Folder[];
  models: STLModel[];
  currentFolderId: string;
  selectedModelId: string | null;
  sidebarOpen: boolean;
};
