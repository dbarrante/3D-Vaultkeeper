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

export type AppState = {
  folders: Folder[];
  models: STLModel[];
  currentFolderId: string;
  selectedModelId: string | null;
  sidebarOpen: boolean;
};
