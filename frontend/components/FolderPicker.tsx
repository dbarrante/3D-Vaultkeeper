// frontend/components/FolderPicker.tsx
import React from "react";
import { X, FolderInput } from "lucide-react";
import { RichTreeView } from "@mui/x-tree-view/RichTreeView";
import { Folder, STLModel, FILE_VIEW_UPLOADS_BUCKET_ID } from "../types";
import { useFolderTree } from "../hooks/useFolderTree";

export type FolderPickerTarget =
  | { mode: "logical"; folderId: string | null }
  | { mode: "file"; realPath: string | null };

interface FolderPickerProps {
  open: boolean;
  viewMode: "logical" | "file";
  folders: Folder[];
  models: STLModel[];
  trackedFolderPaths: string[];
  title: string;
  // Whether a root/library-root option is offered at all. Must be false for
  // Logical-view Move: models.folderId is a NOT NULL database column, so
  // there is no "no folder" a model can be moved to in that mode -- offering
  // one there would send folderId: null and hit a constraint violation.
  // Root IS valid for a File-view move (a file can sit flat in the library
  // root -- that's what the existing "Uploads" bucket already represents)
  // and for picking a parent when creating a new folder in either mode
  // (folders.parentId and file-view's parentPath are both genuinely
  // nullable there).
  allowRoot: boolean;
  onSelect: (target: FolderPickerTarget) => void;
  onClose: () => void;
}

// Synthetic id for the root/library-root node this component injects ahead
// of the real tree. Prefixed distinctly from every id useFolderTree can ever
// produce (real folder UUIDs, "file/..." path ids, or FILE_VIEW_UPLOADS_BUCKET_ID)
// so it can never collide with a real node.
const FOLDER_PICKER_ROOT_ID = "__folder_picker_root__";

export default function FolderPicker({
  open,
  viewMode,
  folders,
  models,
  trackedFolderPaths,
  title,
  allowRoot,
  onSelect,
  onClose,
}: FolderPickerProps) {
  const folderTree = useFolderTree(viewMode, folders, models, trackedFolderPaths);

  if (!open) return null;

  const rootLabel = viewMode === "logical" ? "Root" : "Library Root";
  const items = allowRoot
    ? [{ id: FOLDER_PICKER_ROOT_ID, label: rootLabel, children: [] }, ...folderTree.items]
    : folderTree.items;

  const handleSelectedItemsChange = (_event: React.SyntheticEvent, nodeId: string | null) => {
    if (!nodeId) return;

    if (nodeId === FOLDER_PICKER_ROOT_ID) {
      // Unreachable when allowRoot is false since the node is never rendered
      // into items above, but guard explicitly rather than relying on that.
      if (!allowRoot) return;
      onSelect(
        viewMode === "logical" ? { mode: "logical", folderId: null } : { mode: "file", realPath: null },
      );
      return;
    }

    if (viewMode === "logical") {
      onSelect({ mode: "logical", folderId: nodeId });
      return;
    }

    // The synthetic Uploads bucket has no single real subdirectory (it
    // represents flat pre-feature storage) -- isItemDisabled below keeps it
    // unselectable, so realPaths.get should always resolve here, but the
    // fallback keeps this handler safe if that ever changes.
    const realPath = folderTree.realPaths?.get(nodeId) ?? null;
    if (realPath) onSelect({ mode: "file", realPath });
  };

  return (
    <div className="fixed left-0 top-0 z-50 w-full h-full bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-vault-800 border border-vault-600 rounded-xl p-6 w-80 shadow-2xl animate-in zoom-in-95 duration-200 overflow-y-auto max-h-[80vh]">
        <div className="flex justify-between items-center mb-4">
          <h3 className="font-bold text-white flex items-center gap-2">
            <FolderInput className="w-4 h-4" /> {title}
          </h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="max-h-64 overflow-y-auto mb-2">
          <RichTreeView
            items={items}
            onSelectedItemsChange={handleSelectedItemsChange}
            isItemDisabled={(item) => item.id === FILE_VIEW_UPLOADS_BUCKET_ID}
          />
        </div>
      </div>
    </div>
  );
}
