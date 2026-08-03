import {
  Folder,
  STLModel,
  StorageStats,
  STLModelCollection,
  WatchFolder,
  InboxItem,
} from "../types";

// The Docker image bakes VITE_API_URL as the literal placeholder token
// "TERA_API_URL" (see frontend/vite.config.ts) and relies on env.sh
// sed-replacing it with a real URL at container start. The desktop
// PyInstaller build (desktop/build.ps1) runs a plain `bun run build` with
// no such substitution step, so that placeholder ships unreplaced —
// every fetch resolved to a bogus relative path like "/TERA_API_URL/api/...",
// which fell through app/main.py's catch-all StaticFiles mount and came
// back "Method Not Allowed" for any POST (StaticFiles only serves GET/HEAD).
// The desktop launcher always serves the frontend and API from the same
// origin on one dynamically-chosen port, so treating this exact unreplaced
// placeholder as "use the page's own origin" is always correct there —
// unlike a genuinely empty VITE_API_URL (Docker env var never set), which
// must keep surfacing as the "API Host Not Set" error (see App.tsx).
const UNSUBSTITUTED_PLACEHOLDER = "TERA_API_URL";

// Resolved fresh on every call, not cached at module-load time. The
// localStorage override (set via Settings -> API Host) is meant to let you
// point the app at a different backend without rebuilding — but a plain
// module-level constant only reads localStorage once, when this file is
// first imported. Setting the override afterward updates localStorage
// correctly, yet every fetch in this file kept using the stale value forever
// (no full page reload prompted anywhere), which is exactly why "Add Watched
// Folder" stayed disabled and inbox/watch-folder requests came back as
// "Unexpected token '<', <!DOCTYPE" — they were hitting the Vite dev server's
// own SPA-fallback HTML instead of the real backend.
export function resolveApiOrigin(): string {
  const override = localStorage.getItem("api-port-override");
  if (override) return override;
  const configured = import.meta.env.VITE_API_URL;
  if (configured === UNSUBSTITUTED_PLACEHOLDER) return window.location.origin;
  return configured;
}

function getApiBaseUrl(): string {
  return resolveApiOrigin() + "/api";
}

// --- API SERVICE ---

export type SlicerType =
  | "orcaslicer"
  | "bambu"
  | "elegoo"
  | "flashforge"
  | "prusaslicer"
  | "cura";

export interface SlicerConfig {
  name: string;
  protocol: string;
}

export interface IntegrationTokenStatus {
  configured: boolean;
}

export const SLICERS: Record<SlicerType, SlicerConfig> = {
  orcaslicer: { name: "OrcaSlicer", protocol: "orcaslicer://open?file=" },
  bambu: { name: "Bambu Studio", protocol: "bambustudio://open?file=" },
  elegoo: { name: "Elegoo Slicer", protocol: "elegooslicer://open?file=" },
  flashforge: { name: "FlashPrint", protocol: "flashprint://open?file=" },
  prusaslicer: { name: "PrusaSlicer", protocol: "prusaslicer://open?file=" },
  cura: { name: "Cura", protocol: "cura://open?file=" },
};

export const ALL_SLICER_TYPES = Object.keys(SLICERS) as SlicerType[];

const getSlicerPreference = (): SlicerType => {
  const saved = localStorage.getItem("stlvault-slicer");
  return saved && saved in SLICERS ? (saved as SlicerType) : "orcaslicer";
};

export const getEnabledLaunchSlicers = (): SlicerType[] => {
  const saved = localStorage.getItem("stlvault-launch-slicers");
  if (!saved) return [getSlicerPreference()];
  console.log(saved);
  try {
    const parsed = JSON.parse(saved);
    const enabled = Array.isArray(parsed)
      ? parsed.filter((slicer): slicer is SlicerType => slicer in SLICERS)
      : [];
    return enabled.length > 0 ? enabled : [getSlicerPreference()];
  } catch {
    return [getSlicerPreference()];
  }
};

export const setEnabledLaunchSlicers = (slicers: SlicerType[]) => {
  const enabled = slicers.filter((slicer) => slicer in SLICERS);
  localStorage.setItem(
    "stlvault-launch-slicers",
    JSON.stringify(enabled.length ? enabled : [getSlicerPreference()]),
  );
  localStorage.setItem("stlvault-slicer", enabled[0] || getSlicerPreference());
};

export const api = {
  // 1. GET Folders
  getFolders: async (): Promise<Folder[]> => {
    const res = await fetch(`${getApiBaseUrl()}/folders`);
    if (!res.ok) throw new Error("Failed to fetch folders");
    return res.json();
  },

  // 2. CREATE Folder
  createFolder: async (
    name: string,
    parentId: string | null = null,
  ): Promise<Folder> => {
    const res = await fetch(`${getApiBaseUrl()}/folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parentId }),
    });
    if (!res.ok) throw new Error("Failed to create folder");
    return res.json();
  },

  // 3. UPDATE Folder (Rename/Move)
  updateFolder: async (id: string, name: string): Promise<Folder> => {
    const res = await fetch(`${getApiBaseUrl()}/folders/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) throw new Error("Update failed");
    return res.json();
  },

  // 4. DELETE Folder
  deleteFolder: async (id: string): Promise<void> => {
    const res = await fetch(`${getApiBaseUrl()}/folders/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Delete failed");
  },

  // 5. GET Models
  getModels: async (folderId?: string): Promise<STLModel[]> => {
    const query = folderId && folderId !== "all" ? `?folderId=${folderId}` : "";
    const res = await fetch(`${getApiBaseUrl()}/models${query}`);
    if (!res.ok) throw new Error("Failed to fetch models");
    return res.json();
  },

  // 6. UPLOAD Model
  uploadModel: async (
    file: File,
    folderId: string,
    thumbnail?: string,
    tags: string[] = [],
  ): Promise<STLModel> => {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("folderId", folderId);
    if (thumbnail) formData.append("thumbnail", thumbnail); // Send base64 thumbnail
    if (tags.length > 0) formData.append("tags", JSON.stringify(tags));

    const res = await fetch(`${getApiBaseUrl()}/models/upload`, {
      method: "POST",
      body: formData,
    });

    if (!res.ok) throw new Error("Upload failed");
    return res.json();
  },

  // 7. UPDATE Model
  updateModel: async (
    id: string,
    updates: Partial<STLModel>,
  ): Promise<STLModel> => {
    const res = await fetch(`${getApiBaseUrl()}/models/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error("Update failed");
    return res.json();
  },

  // 8. DELETE Model
  deleteModel: async (id: string, deleteFile: boolean = false): Promise<void> => {
    console.log("API: Deleting model", id, "deleteFile:", deleteFile);

    const res = await fetch(`${getApiBaseUrl()}/models/${id}?deleteFile=${deleteFile}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Delete failed");
  },

  // 9. GET Download URL
  getDownloadUrl: (model: STLModel) => {
    return `${getApiBaseUrl()}/models/${model.id}/download`;
  },

  //9b. GET slicer Weblink
  getSlicerUrl: (model: STLModel, slicer?: SlicerType) => {
    const modelURL = `${getApiBaseUrl()}/models/${model.id}/download`;

    // Get user's preferred slicer from localStorage
    let slicerPreference =
      localStorage.getItem("stlvault-slicer") || "orcaslicer";

    if (slicer) {
      slicerPreference = slicer;
    }

    const protocol =
      SLICERS[slicerPreference as SlicerType] || SLICERS["orcaslicer"];
    return `${protocol.protocol}${modelURL}`;
  },

  // 10. BULK DELETE
  bulkDeleteModels: async (ids: string[]): Promise<void> => {
    console.log("API: Bulk deleting models", ids);

    const res = await fetch(`${getApiBaseUrl()}/models/bulk-delete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    if (!res.ok) throw new Error("Bulk delete failed");
  },

  // 11. BULK MOVE
  bulkMoveModels: async (ids: string[], folderId: string): Promise<void> => {
    const res = await fetch(`${getApiBaseUrl()}/models/bulk-move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, folderId }),
    });
    if (!res.ok) throw new Error("Bulk move failed");
  },

  // 12. BULK TAG
  bulkAddTags: async (ids: string[], tags: string[]): Promise<void> => {
    const res = await fetch(`${getApiBaseUrl()}/models/bulk-tag`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, tags }),
    });
    if (!res.ok) throw new Error("Bulk tag failed");
  },

  // 13. RETRIEVE MODEL OPTIONS
  retrieveModelOptions: async (url: string): Promise<STLModelCollection[]> => {
    const res = await fetch(`${getApiBaseUrl()}/import/options`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    if (!res.ok) throw new Error("Import failed");
    return res.json();
  },

  // 13. IMPORT FROM URL
  importModelFromId: async (
    id: string,
    name: string,
    parentId: string,
    previewPath: string,
    folderId: string,
    typeName: string,
    source: string = "printables",
  ): Promise<STLModel> => {
    const res = await fetch(`${getApiBaseUrl()}/import/importid`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source,
        id,
        name,
        parentId,
        previewPath,
        folderId,
        typeName,
      }),
    });
    if (!res.ok) throw new Error("Import failed");
    return res.json();
  },

  getMakerWorldTokenStatus: async (): Promise<IntegrationTokenStatus> => {
    const res = await fetch(`${getApiBaseUrl()}/settings/makerworld-token`);
    if (!res.ok) throw new Error("Failed to fetch MakerWorld token status");
    return res.json();
  },

  updateMakerWorldToken: async (
    token: string,
  ): Promise<IntegrationTokenStatus> => {
    const res = await fetch(`${getApiBaseUrl()}/settings/makerworld-token`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) throw new Error("Failed to update MakerWorld token");
    return res.json();
  },

  clearMakerWorldToken: async (): Promise<IntegrationTokenStatus> => {
    const res = await fetch(`${getApiBaseUrl()}/settings/makerworld-token`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear: true }),
    });
    if (!res.ok) throw new Error("Failed to clear MakerWorld token");
    return res.json();
  },

  // 14. REPLACE FILE
  replaceModelFile: async (
    id: string,
    file: File,
    thumbnail?: string,
  ): Promise<STLModel> => {
    const formData = new FormData();
    formData.append("file", file);
    if (thumbnail) formData.append("thumbnail", thumbnail);

    const res = await fetch(`${getApiBaseUrl()}/models/${id}/file`, {
      method: "PUT",
      body: formData,
    });
    if (!res.ok) throw new Error("File replacement failed");
    return res.json();
  },

  // 14a. REPLACE FILE
  replaceModelThumbnail: async (id: string, file: File): Promise<STLModel> => {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch(`${getApiBaseUrl()}/models/${id}/thumbnail`, {
      method: "PUT",
      body: formData,
    });
    if (!res.ok) throw new Error("File replacement failed");
    return res.json();
  },

  // 14b. GET Manual URL
  getManualUrl: (model: STLModel) => {
    return `${getApiBaseUrl()}/models/${model.id}/manual`;
  },

  // 14c. UPLOAD Manual
  uploadManual: async (id: string, file: File): Promise<STLModel> => {
    const formData = new FormData();
    formData.append("file", file);

    const res = await fetch(`${getApiBaseUrl()}/models/${id}/manual`, {
      method: "PUT",
      body: formData,
    });
    if (!res.ok) throw new Error("Manual upload failed");
    return res.json();
  },

  // 14d. DELETE Manual
  deleteManual: async (id: string): Promise<STLModel> => {
    const res = await fetch(`${getApiBaseUrl()}/models/${id}/manual`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Manual delete failed");
    return res.json();
  },

  // 15. GET Storage Stats
  getStorageStats: async (): Promise<StorageStats> => {
    const res = await fetch(`${getApiBaseUrl()}/storage-stats`);
    if (!res.ok) throw new Error("Failed to fetch storage stats");
    return res.json();
  },

  // 16. WATCH FOLDERS
  getWatchFolders: async (): Promise<WatchFolder[]> => {
    const res = await fetch(`${getApiBaseUrl()}/watch-folders`);
    if (!res.ok) throw new Error("Failed to fetch watch folders");
    return res.json();
  },

  createWatchFolder: async (
    path: string,
    folderId: string,
    frequencyMinutes: number,
  ): Promise<WatchFolder> => {
    const res = await fetch(`${getApiBaseUrl()}/watch-folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, folderId, frequencyMinutes }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || "Failed to create watch folder");
    }
    return res.json();
  },

  deleteWatchFolder: async (id: string): Promise<void> => {
    const res = await fetch(`${getApiBaseUrl()}/watch-folders/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error("Failed to delete watch folder");
  },

  browseFolder: async (): Promise<{ path: string | null }> => {
    const res = await fetch(`${getApiBaseUrl()}/browse-folder`, {
      method: "POST",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || "Folder browser unavailable");
    }
    return res.json();
  },

  scanWatchFolderNow: async (id: string): Promise<{ ingested: number }> => {
    const res = await fetch(`${getApiBaseUrl()}/watch-folders/${id}/scan-now`, {
      method: "POST",
    });
    if (!res.ok) throw new Error("Scan failed");
    return res.json();
  },

  // 17. INBOX
  getInbox: async (): Promise<InboxItem[]> => {
    const res = await fetch(`${getApiBaseUrl()}/inbox`);
    if (!res.ok) throw new Error("Failed to fetch inbox");
    return res.json();
  },

  fileInboxItem: async (id: string, folderId: string): Promise<STLModel> => {
    const res = await fetch(`${getApiBaseUrl()}/inbox/${id}/file`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folderId }),
    });
    if (!res.ok) throw new Error("Failed to file inbox item");
    return res.json();
  },

  dismissInboxItem: async (id: string): Promise<void> => {
    const res = await fetch(`${getApiBaseUrl()}/inbox/${id}/dismiss`, {
      method: "POST",
    });
    if (!res.ok) throw new Error("Failed to dismiss inbox item");
  },

  inboxScanNow: async (): Promise<{ added: number }> => {
    const res = await fetch(`${getApiBaseUrl()}/inbox/scan-now`, {
      method: "POST",
    });
    if (!res.ok) throw new Error("Inbox scan failed");
    return res.json();
  },

  // 18. AI (OpenRouter)
  getOpenRouterKeyStatus: async (): Promise<IntegrationTokenStatus> => {
    const res = await fetch(`${getApiBaseUrl()}/settings/openrouter-key`);
    if (!res.ok) throw new Error("Failed to fetch OpenRouter key status");
    return res.json();
  },

  updateOpenRouterKey: async (
    token: string,
  ): Promise<IntegrationTokenStatus> => {
    const res = await fetch(`${getApiBaseUrl()}/settings/openrouter-key`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) throw new Error("Failed to update OpenRouter key");
    return res.json();
  },

  clearOpenRouterKey: async (): Promise<IntegrationTokenStatus> => {
    const res = await fetch(`${getApiBaseUrl()}/settings/openrouter-key`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ clear: true }),
    });
    if (!res.ok) throw new Error("Failed to clear OpenRouter key");
    return res.json();
  },

  suggestTags: async (modelId: string): Promise<{ tags: string[] }> => {
    const res = await fetch(`${getApiBaseUrl()}/models/${modelId}/suggest-tags`, {
      method: "POST",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || "Failed to suggest tags");
    }
    return res.json();
  },

  suggestPricing: async (
    modelId: string,
  ): Promise<{ priceRange: string; popularity: string; reasoning: string }> => {
    const res = await fetch(
      `${getApiBaseUrl()}/models/${modelId}/suggest-pricing`,
      { method: "POST" },
    );
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail || "Failed to suggest pricing");
    }
    return res.json();
  },
};
