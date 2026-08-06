import React, { useEffect, useState } from "react";
import {
  Eye,
  RefreshCw,
  Trash2,
  Clock,
  FolderInput,
  FolderOpen,
  Check,
  X,
} from "lucide-react";
import { api } from "../services/api";
import { Folder, WatchFolder } from "../types";
import ImportWizard from "./ImportWizard";

const NEW_FOLDER_SENTINEL = "__new__";

function formatRelativeTime(ms: number | null): string {
  if (!ms) return "Never scanned";
  const diffSeconds = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (diffSeconds < 60) return "Just now";
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.floor(diffHours / 24)}d ago`;
}

const WatcherInbox: React.FC = () => {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [watchFolders, setWatchFolders] = useState<WatchFolder[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newPath, setNewPath] = useState("");
  const [newFolderId, setNewFolderId] = useState("");
  const [newFrequency, setNewFrequency] = useState(60);
  const [scanningId, setScanningId] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState(false);
  const [browseNote, setBrowseNote] = useState<string | null>(null);

  const [importRootPath, setImportRootPath] = useState<string | null>(null);
  const [importBrowsing, setImportBrowsing] = useState(false);

  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [folderCreateError, setFolderCreateError] = useState<string | null>(null);

  const refresh = async () => {
    const [f, w] = await Promise.all([
      api.getFolders(),
      api.getWatchFolders(),
    ]);
    setFolders(f);
    setWatchFolders(w);
    if (!newFolderId && f.length > 0) setNewFolderId(f[0].id);
  };

  useEffect(() => {
    refresh()
      .catch((e) => setError(e.message || "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  const handleBrowseFolder = async () => {
    setBrowsing(true);
    setBrowseNote(null);
    try {
      const { path } = await api.browseFolder();
      if (path) setNewPath(path);
      // path === null means the user opened the dialog and hit Cancel — leave
      // whatever they'd already typed alone rather than clearing it.
    } catch (err: any) {
      setBrowseNote(err.message || "Folder browser unavailable — type the path manually");
    } finally {
      setBrowsing(false);
    }
  };

  const handleStartImport = async () => {
    setImportBrowsing(true);
    try {
      const { path } = await api.browseFolder();
      if (path) setImportRootPath(path);
    } catch (err: any) {
      setError(err.message || "Folder browser unavailable");
    } finally {
      setImportBrowsing(false);
    }
  };

  const handleFolderSelectChange = (value: string) => {
    if (value === NEW_FOLDER_SENTINEL) {
      setCreatingFolder(true);
      setFolderCreateError(null);
      return;
    }
    setNewFolderId(value);
  };

  const handleCreateFolder = async () => {
    if (!newFolderName.trim()) return;
    setFolderCreateError(null);
    try {
      const created = await api.createFolder(newFolderName.trim(), null);
      setFolders((prev) => [...prev, created]);
      setNewFolderId(created.id);
      setNewFolderName("");
      setCreatingFolder(false);
    } catch (err: any) {
      setFolderCreateError(err.message || "Failed to create folder");
    }
  };

  const handleAddWatchFolder = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newPath.trim() || !newFolderId) return;
    setError(null);
    try {
      await api.createWatchFolder(newPath.trim(), newFolderId, newFrequency);
      setNewPath("");
      await refresh();
    } catch (err: any) {
      setError(err.message || "Failed to add watch folder");
    }
  };

  const handleDeleteWatchFolder = async (id: string) => {
    await api.deleteWatchFolder(id);
    setWatchFolders((prev) => prev.filter((w) => w.id !== id));
  };

  const handleScanNow = async (id: string) => {
    setScanningId(id);
    try {
      await api.scanWatchFolderNow(id);
      await refresh();
    } catch (err: any) {
      setError(err.message || "Scan failed");
    } finally {
      setScanningId(null);
    }
  };

  if (loading) {
    return (
      <div className="mb-8 text-sm text-slate-500">
        Loading watched folders...
      </div>
    );
  }

  return (
    <>
      {error && (
        <div className="mb-6 p-3 rounded-lg bg-red-900/30 border border-red-800 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Watched Folders */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <Eye className="w-5 h-5 text-blue-400" />
          <h3 className="text-lg font-semibold text-white">
            Watched Folders
          </h3>
        </div>
        <p className="text-sm text-slate-400 mb-4">
          Point at a folder and STL Vault scans it on a schedule, auto-adding
          any new supported 3D files (.stl, .3mf, .obj, .step, .stp) into the
          library folder you choose. Existing files in the target folder are
          never touched.
        </p>

        <button
          onClick={handleStartImport}
          disabled={importBrowsing}
          className="flex items-center gap-2 px-3 py-2 bg-vault-800 hover:bg-vault-700 rounded text-sm text-slate-200 disabled:opacity-50"
        >
          <FolderInput className="w-4 h-4" />
          {importBrowsing ? "..." : "Import from folder..."}
        </button>

        {watchFolders.length > 0 && (
          <div className="space-y-2 mb-4">
            {watchFolders.map((w) => {
              const targetFolder = folders.find((f) => f.id === w.folderId);
              return (
                <div
                  key={w.id}
                  className="flex items-center justify-between gap-3 p-3 bg-vault-800 border border-vault-700 rounded-lg"
                >
                  <div className="min-w-0">
                    <p className="text-sm text-white truncate" title={w.path}>
                      {w.path}
                    </p>
                    <p className="text-xs text-slate-500 flex items-center gap-2 mt-0.5">
                      <span>→ {targetFolder?.name || "Unknown folder"}</span>
                      <span>·</span>
                      <span>every {w.frequencyMinutes}m</span>
                      <span>·</span>
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {formatRelativeTime(w.lastScanAt)}
                      </span>
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={() => handleScanNow(w.id)}
                      disabled={scanningId === w.id}
                      className="p-2 rounded-lg hover:bg-vault-700 text-slate-300 hover:text-blue-400 transition-colors disabled:opacity-50"
                      title="Scan now"
                    >
                      <RefreshCw
                        className={`w-4 h-4 ${scanningId === w.id ? "animate-spin" : ""}`}
                      />
                    </button>
                    <button
                      onClick={() => handleDeleteWatchFolder(w.id)}
                      className="p-2 rounded-lg hover:bg-vault-700 text-slate-300 hover:text-red-400 transition-colors"
                      title="Stop watching"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        <form
          onSubmit={handleAddWatchFolder}
          className="p-4 bg-vault-800 rounded-lg border border-vault-700"
        >
          <div className="grid grid-cols-1 sm:grid-cols-[2fr_1fr_1fr] gap-3 mb-1">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Folder path
              </label>
              <div className="flex gap-2">
                <input
                  type="text"
                  className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white text-sm focus:border-blue-500 outline-none placeholder:text-slate-600"
                  placeholder="C:\Users\you\3D Models\Downloads"
                  value={newPath}
                  onChange={(e) => setNewPath(e.target.value)}
                />
                <button
                  type="button"
                  onClick={handleBrowseFolder}
                  disabled={browsing}
                  title="Browse for a folder on this server"
                  className="shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-md bg-vault-700 hover:bg-vault-600 text-slate-200 text-sm transition-colors disabled:opacity-50"
                >
                  <FolderOpen className="w-4 h-4" />
                  {browsing ? "..." : "Browse"}
                </button>
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Add to
              </label>
              {creatingFolder ? (
                <div className="flex gap-1">
                  <input
                    autoFocus
                    type="text"
                    className="w-full bg-vault-900 border border-blue-500 rounded-md px-3 py-2 text-white text-sm outline-none placeholder:text-slate-600"
                    placeholder="New folder name"
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleCreateFolder();
                      }
                      if (e.key === "Escape") setCreatingFolder(false);
                    }}
                  />
                  <button
                    type="button"
                    onClick={handleCreateFolder}
                    disabled={!newFolderName.trim()}
                    title="Create folder"
                    className="shrink-0 flex items-center justify-center w-9 rounded-md bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
                  >
                    <Check className="w-4 h-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setCreatingFolder(false)}
                    title="Cancel"
                    className="shrink-0 flex items-center justify-center w-9 rounded-md bg-vault-700 hover:bg-vault-600 text-slate-300"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <select
                  className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white text-sm focus:border-blue-500 outline-none"
                  value={newFolderId}
                  onChange={(e) => handleFolderSelectChange(e.target.value)}
                >
                  {folders.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
                  <option value={NEW_FOLDER_SENTINEL}>+ New folder...</option>
                </select>
              )}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Every (minutes)
              </label>
              <input
                type="number"
                min={1}
                className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white text-sm focus:border-blue-500 outline-none"
                value={newFrequency}
                onChange={(e) => setNewFrequency(Number(e.target.value) || 1)}
              />
            </div>
          </div>
          {browseNote && (
            <p className="text-xs text-amber-400 mb-2">{browseNote}</p>
          )}
          {folderCreateError && (
            <p className="text-xs text-red-400 mb-2">{folderCreateError}</p>
          )}
          <button
            type="submit"
            disabled={!newPath.trim() || !newFolderId}
            className="mt-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Add Watched Folder
          </button>
        </form>
      </div>

      {importRootPath && (
        <ImportWizard
          rootPath={importRootPath}
          folders={folders}
          onClose={() => setImportRootPath(null)}
          onComplete={refresh}
        />
      )}
    </>
  );
};

export default WatcherInbox;
