import React, { useEffect, useState } from "react";
import { Eye, Inbox as InboxIcon, RefreshCw, Trash2, Clock, FolderInput } from "lucide-react";
import { api } from "../services/api";
import { Folder, WatchFolder, InboxItem } from "../types";

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

function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}

const WatcherInbox: React.FC = () => {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [watchFolders, setWatchFolders] = useState<WatchFolder[]>([]);
  const [inboxItems, setInboxItems] = useState<InboxItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [newPath, setNewPath] = useState("");
  const [newFolderId, setNewFolderId] = useState("");
  const [newFrequency, setNewFrequency] = useState(60);
  const [scanningId, setScanningId] = useState<string | null>(null);
  const [inboxScanning, setInboxScanning] = useState(false);

  const [inboxFolderChoice, setInboxFolderChoice] = useState<
    Record<string, string>
  >({});

  const refresh = async () => {
    const [f, w, i] = await Promise.all([
      api.getFolders(),
      api.getWatchFolders(),
      api.getInbox(),
    ]);
    setFolders(f);
    setWatchFolders(w);
    setInboxItems(i);
    if (!newFolderId && f.length > 0) setNewFolderId(f[0].id);
  };

  useEffect(() => {
    refresh()
      .catch((e) => setError(e.message || "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

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

  const handleInboxScanNow = async () => {
    setInboxScanning(true);
    try {
      await api.inboxScanNow();
      await refresh();
    } catch (err: any) {
      setError(err.message || "Inbox scan failed");
    } finally {
      setInboxScanning(false);
    }
  };

  const handleFileItem = async (id: string) => {
    const folderId = inboxFolderChoice[id] || folders[0]?.id;
    if (!folderId) return;
    await api.fileInboxItem(id, folderId);
    setInboxItems((prev) => prev.filter((i) => i.id !== id));
  };

  const handleDismissItem = async (id: string) => {
    await api.dismissInboxItem(id);
    setInboxItems((prev) => prev.filter((i) => i.id !== id));
  };

  if (loading) {
    return (
      <div className="mb-8 text-sm text-slate-500">
        Loading watched folders and inbox...
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
          <div className="grid grid-cols-1 sm:grid-cols-[2fr_1fr_1fr] gap-3 mb-3">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Folder path
              </label>
              <input
                type="text"
                className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white text-sm focus:border-blue-500 outline-none placeholder:text-slate-600"
                placeholder="C:\Users\you\3D Models\Downloads"
                value={newPath}
                onChange={(e) => setNewPath(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">
                Add to
              </label>
              <select
                className="w-full bg-vault-900 border border-vault-700 rounded-md px-3 py-2 text-white text-sm focus:border-blue-500 outline-none"
                value={newFolderId}
                onChange={(e) => setNewFolderId(e.target.value)}
              >
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
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
          <button
            type="submit"
            disabled={!newPath.trim() || !newFolderId}
            className="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Add Watched Folder
          </button>
        </form>
      </div>

      {/* Inbox */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <InboxIcon className="w-5 h-5 text-blue-400" />
            <h3 className="text-lg font-semibold text-white">
              Inbox
              {inboxItems.length > 0 && (
                <span className="ml-2 inline-flex items-center justify-center min-w-[1.5rem] h-6 px-1.5 rounded-full bg-blue-600 text-white text-xs font-bold">
                  {inboxItems.length}
                </span>
              )}
            </h3>
          </div>
          <button
            onClick={handleInboxScanNow}
            disabled={inboxScanning}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-vault-800 border border-vault-700 hover:border-vault-600 text-slate-300 hover:text-white text-sm transition-colors disabled:opacity-50"
          >
            <RefreshCw
              className={`w-3.5 h-3.5 ${inboxScanning ? "animate-spin" : ""}`}
            />
            Check Downloads now
          </button>
        </div>
        <p className="text-sm text-slate-400 mb-4">
          New 3D files that show up in your Downloads folder land here first
          — nothing is added to the library until you file it into a folder
          or dismiss it.
        </p>

        {inboxItems.length === 0 ? (
          <div className="p-4 bg-vault-800/50 border border-vault-700 rounded-lg text-sm text-slate-500 text-center">
            Nothing waiting to be filed.
          </div>
        ) : (
          <div className="space-y-2">
            {inboxItems.map((item) => (
              <div
                key={item.id}
                className="flex items-center justify-between gap-3 p-3 bg-vault-800 border border-vault-700 rounded-lg"
              >
                <div className="min-w-0">
                  <p className="text-sm text-white truncate">
                    {basename(item.path)}
                  </p>
                  <p className="text-xs text-slate-500 truncate" title={item.path}>
                    {item.path}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <select
                    className="bg-vault-900 border border-vault-700 rounded-md px-2 py-1.5 text-white text-xs focus:border-blue-500 outline-none"
                    value={inboxFolderChoice[item.id] || folders[0]?.id || ""}
                    onChange={(e) =>
                      setInboxFolderChoice((prev) => ({
                        ...prev,
                        [item.id]: e.target.value,
                      }))
                    }
                  >
                    {folders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => handleFileItem(item.id)}
                    className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium transition-colors"
                    title="Add to library"
                  >
                    <FolderInput className="w-3.5 h-3.5" />
                    File
                  </button>
                  <button
                    onClick={() => handleDismissItem(item.id)}
                    className="px-3 py-1.5 rounded-lg bg-vault-700 hover:bg-vault-600 text-slate-300 hover:text-white text-xs font-medium transition-colors"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
};

export default WatcherInbox;
