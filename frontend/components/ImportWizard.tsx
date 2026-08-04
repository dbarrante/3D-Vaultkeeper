import React, { useEffect, useState } from "react";
import { Folder as FolderIcon, File as FileIcon, X, Plus } from "lucide-react";
import { api } from "../services/api";
import { Folder, ImportTreeNode, ImportPlacement } from "../types";

interface ImportWizardProps {
  rootPath: string;
  folders: Folder[];
  onClose: () => void;
  onComplete: () => void;
}

interface StagedPlacement extends ImportPlacement {
  sourceLabel: string; // display name shown in the review step
  targetLabel: string;
}

const ImportWizard: React.FC<ImportWizardProps> = ({
  rootPath,
  folders: initialFolders,
  onClose,
  onComplete,
}) => {
  const [tree, setTree] = useState<ImportTreeNode | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [folders, setFolders] = useState<Folder[]>(initialFolders);
  const [placements, setPlacements] = useState<StagedPlacement[]>([]);
  const [creatingUnderId, setCreatingUnderId] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState("");

  useEffect(() => {
    api
      .getImportTree(rootPath)
      .then(setTree)
      .catch((e) => setTreeError(e.message || "Failed to read directory"));
  }, [rootPath]);

  const folderLabel = (id: string): string => {
    const chain: string[] = [];
    let current = folders.find((f) => f.id === id);
    while (current) {
      chain.unshift(current.name);
      current = current.parentId
        ? folders.find((f) => f.id === current!.parentId)
        : undefined;
    }
    return chain.join(" / ") || id;
  };

  const stagePlacement = (sourcePath: string, isFolder: boolean, sourceLabel: string, targetFolderId: string) => {
    setPlacements((prev) => [
      ...prev.filter((p) => p.sourcePath !== sourcePath),
      { sourcePath, isFolder, targetFolderId, sourceLabel, targetLabel: folderLabel(targetFolderId) },
    ]);
  };

  const handleCreateFolder = async (parentId: string | null) => {
    if (!newFolderName.trim()) return;
    const created = await api.createFolder(newFolderName.trim(), parentId);
    setFolders((prev) => [...prev, created]);
    setNewFolderName("");
    setCreatingUnderId(null);
  };

  const rootFolders = folders.filter((f) => f.parentId === null);
  const childFolders = (parentId: string) => folders.filter((f) => f.parentId === parentId);

  const renderRawNode = (node: { name: string; path: string }, isFolder: boolean) => (
    <div
      key={node.path}
      draggable
      onDragStart={(e) => e.dataTransfer.setData("text/plain", JSON.stringify({ path: node.path, isFolder }))}
      className="flex items-center gap-2 px-2 py-1 rounded cursor-grab hover:bg-vault-800"
    >
      {isFolder ? <FolderIcon className="w-4 h-4 text-blue-400" /> : <FileIcon className="w-4 h-4 text-slate-400" />}
      <span className="text-sm truncate">{node.name}</span>
      {placements.some((p) => p.sourcePath === node.path) && (
        <span className="text-xs text-green-400 ml-auto">
          → {placements.find((p) => p.sourcePath === node.path)?.targetLabel}
        </span>
      )}
    </div>
  );

  const renderLogicalNode = (folder: Folder) => (
    <div
      key={folder.id}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        const data = e.dataTransfer.getData("text/plain");
        if (!data) return;
        const { path, isFolder } = JSON.parse(data);
        const label = path.split(/[\\/]/).pop() || path;
        stagePlacement(path, isFolder, label, folder.id);
      }}
      className="pl-2 py-1 rounded hover:bg-vault-800 border border-dashed border-vault-700"
    >
      <div className="flex items-center gap-2">
        <FolderIcon className="w-4 h-4 text-blue-400" />
        <span className="text-sm">{folder.name}</span>
        <button
          className="ml-auto text-xs text-slate-400 hover:text-white"
          onClick={() => setCreatingUnderId(folder.id)}
        >
          <Plus className="w-3 h-3" />
        </button>
      </div>
      {creatingUnderId === folder.id && (
        <div className="flex gap-1 pl-6 py-1">
          <input
            autoFocus
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleCreateFolder(folder.id)}
            className="bg-vault-900 border border-vault-700 rounded px-1 text-sm"
            placeholder="New folder name"
          />
        </div>
      )}
      <div className="pl-4">{childFolders(folder.id).map(renderLogicalNode)}</div>
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center">
      <div className="bg-vault-900 border border-vault-700 rounded-xl w-[90vw] h-[85vh] flex flex-col p-4">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-lg font-semibold text-white">Import from folder</h2>
          <button onClick={onClose} aria-label="Close"><X className="w-5 h-5" /></button>
        </div>
        {treeError && <p className="text-red-400 text-sm mb-2">{treeError}</p>}
        <div className="flex-1 flex gap-4 overflow-hidden">
          <div className="flex-1 overflow-y-auto border border-vault-700 rounded p-2">
            <p className="text-xs text-slate-500 mb-2">On disk: {rootPath}</p>
            {tree && (
              <>
                {tree.folders.map((f) => renderRawNode(f, true))}
                {tree.files.map((f) => renderRawNode(f, false))}
              </>
            )}
          </div>
          <div className="flex-1 overflow-y-auto border border-vault-700 rounded p-2">
            <p className="text-xs text-slate-500 mb-2">Your library</p>
            {rootFolders.map(renderLogicalNode)}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ImportWizard;
