import React, { useState, useMemo, useCallback, useEffect } from "react";
import {
  Folder as FolderIcon,
  Plus,
  Box,
  LayoutGrid,
  Pencil,
  Trash2,
  Check,
  X,
  ChevronRight,
  Settings,
  PlusIcon,
} from "lucide-react";
import { Folder, STLModel, StorageStats } from "../types";
import { FILE_VIEW_UPLOADS_BUCKET_ID, fileViewSegments, fileViewFolderSegments } from "../types";

import Stack from "@mui/material/Stack";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import { TreeViewDefaultItemModelProperties } from "@mui/x-tree-view/models";
import { useTreeItemUtils } from "@mui/x-tree-view/hooks";
import {
  UseTreeItemContentSlotOwnProps,
  UseTreeItemLabelSlotOwnProps,
  UseTreeItemStatus,
} from "@mui/x-tree-view/useTreeItem";
import { RichTreeView } from "@mui/x-tree-view/RichTreeView";
import { SimpleTreeView } from "@mui/x-tree-view/SimpleTreeView";
import {
  TreeItem,
  TreeItemProps,
  TreeItemSlotProps,
} from "@mui/x-tree-view/TreeItem";
import Container from "@mui/material/Container";
import Button from "@mui/material/Button";
import OutlinedInput from "@mui/material/OutlinedInput";
import Badge from "@mui/material/Badge";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";

import { api } from "../services/api";

const APP_TAG = import.meta.env.VITE_APP_TAG || "dev";

interface SidebarProps {
  folders: Folder[];
  models: STLModel[];
  currentFolderId: string;
  storageStats: StorageStats;
  onSelectFolder: (id: string) => void;
  onCreateFolder: (name: string, parentId: string | null) => void;
  onRenameFolder: (id: string, newName: string) => void;
  onDeleteFolder: (id: string) => void;
  onMoveToFolder: (folderId: string, modelIds: string[]) => void;
  onUploadToFolder: (folderId: string, files: FileList) => void;
  onOpenSettings: () => void;
  variant?: "desktop" | "mobile";
  // Controlled by App.tsx (Task 10) -- Sidebar no longer owns this as
  // local state, since the mobile drawer unmounts/remounts on close/open
  // and a local useState would silently reset to "logical" on reopen,
  // desyncing from App's own copy used for grid filtering.
  viewMode: "logical" | "file";
  onViewModeChange?: (mode: "logical" | "file") => void;
  onFileViewMutated: () => void;
  trackedFolderPaths: string[];
}

const Sidebar: React.FC<SidebarProps> = ({
  folders,
  models,
  currentFolderId,
  storageStats,
  onSelectFolder,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onMoveToFolder,
  onUploadToFolder,
  onOpenSettings,
  variant = "desktop",
  viewMode,
  onViewModeChange,
  onFileViewMutated,
  trackedFolderPaths,
}) => {
  const isDesktopVariant = variant === "desktop";
  const [isCreatingRoot, setIsCreatingRoot] = useState(false);
  const [newRootName, setNewRootName] = useState("");

  // State for tree interactions
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creatingSubfolderId, setCreatingSubfolderId] = useState<string | null>(
    null,
  );
  const [dragTargetId, setDragTargetId] = useState<string | null>(null);

  // Resize state
  const [width, setWidth] = useState(330);
  const [isResizing, setIsResizing] = useState(false);

  const startResizing = useCallback(
    (e: React.MouseEvent) => {
      if (!isDesktopVariant) return;
      e.preventDefault(); // Prevent text selection
      setIsResizing(true);
    },
    [isDesktopVariant],
  );

  // Calculate direct counts only (not recursive, matching file system behavior usually)
  const folderCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    models.forEach((m) => {
      counts[m.folderId] = (counts[m.folderId] || 0) + 1;
    });
    folders.forEach((f) => {
      counts[f.parentId] = (counts[f.parentId] || 0) + 1;
    });
    return counts;
  }, [models, folders]);

  useEffect(() => {
    if (!isDesktopVariant) return;
    if (!isResizing) return;

    const handleMouseMove = (e: MouseEvent) => {
      // Limit width between 200px and 600px
      const newWidth = Math.min(Math.max(e.clientX, 200), 600);
      setWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);

    // Add grabbing cursor to body during resize
    document.body.style.cursor = "col-resize";

    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      document.body.style.cursor = "";
    };
  }, [isResizing, isDesktopVariant]);

  // Auto-expand the current folder itself (so its own subfolders become
  // visible the moment you navigate into it — this was the actual gap:
  // the original version only walked *ancestors*, so selecting "Vehicles"
  // never revealed "Tanks" inside it, only navigating into "Tanks" itself
  // would expand "Vehicles" around it) and all of its ancestors (so a
  // deep selection still shows its own position in the tree).
  useEffect(() => {
    if (currentFolderId && currentFolderId !== "all") {
      const expandPath = (id: string, path: Set<string>) => {
        path.add(id);
        const folder = folders.find((f) => f.id === id);
        if (folder && folder.parentId) {
          expandPath(folder.parentId, path);
        }
      };

      setExpandedIds((prev) => {
        const next = new Set<string>(prev);
        expandPath(currentFolderId, next);
        return next;
      });
    }
  }, [currentFolderId, folders]);

  const handleCreateFolderSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newRootName.trim() && !creatingSubfolderId) {
      onCreateFolder(newRootName.trim(), null);
      setNewRootName("");
      setIsCreatingRoot(false);
    } else if (newRootName.trim() && creatingSubfolderId != "") {
      onCreateFolder(newRootName.trim(), creatingSubfolderId);
      setNewRootName("");
      setIsCreatingRoot(false);
      setCreatingSubfolderId("");
    }
  };

  // Fires when the user manually clicks a tree item's expand/collapse
  // arrow. Passing `expandedItems` below puts RichTreeView in controlled
  // mode, which means it stops managing its own expand/collapse state
  // entirely -- without this handler keeping `expandedIds` in sync,
  // manual toggling would silently stop working the moment the tree
  // becomes controlled.
  const handleItemExpansionToggle = (
    _event: React.SyntheticEvent | null,
    itemId: string,
    isExpanded: boolean,
  ) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (isExpanded) next.add(itemId);
      else next.delete(itemId);
      return next;
    });
  };

  const handleDeleteRequest = (id: string, count: number) => {
    if (count > 0) {
      alert("Folder must be empty to delete (no files and no subfolders).");
      return;
    }
    onDeleteFolder(id);
  };

  // Drag Handlers
  const handleDragOver = (e: React.DragEvent, folderId: string) => {
    e.preventDefault();
    e.stopPropagation();
    if (dragTargetId !== folderId) {
      setDragTargetId(folderId);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent, folderId: string) => {
    e.preventDefault();
    e.stopPropagation();
    setDragTargetId(null);

    // Check for Files first (Upload to folder)
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      onUploadToFolder(folderId, e.dataTransfer.files);
      return;
    }

    // Check for internal move (Move existing cards to folder)
    try {
      const data = e.dataTransfer.getData("application/json");
      if (data) {
        const { modelIds } = JSON.parse(data);
        if (Array.isArray(modelIds) && modelIds.length > 0) {
          onMoveToFolder(folderId, modelIds);
        }
      }
    } catch (err) {
      console.error("Failed to process drop", err);
    }
  };

  // Format Storage Display
  const formatSize = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  };

  const percentUsed =
    storageStats.total > 0
      ? Math.min((storageStats.used / storageStats.total) * 100, 100)
      : 0;

  // Root folders
  const rootFolders = folders.filter((f) => f.parentId === null);

  //builds the treeview structure
  const treefolders = () => {
    const treeitems: TreeViewDefaultItemModelProperties[] = [];
    rootFolders.map((folder) => {
      treeitems.push({
        id: folder.id,
        label: folder.name,
        children: [],
      });
    });
    treeitems.map((folder) => {
      folders.map((subfolder) => {
        if (subfolder.parentId === folder.id) {
          folder.children.push({ id: subfolder.id, label: subfolder.name });
        }
      });
      folder.children.sort((a, b) => {
        return a.label.localeCompare(b.label);
      });
    });
    treeitems.sort((a, b) => {
      return a.label.localeCompare(b.label);
    });
    return treeitems;
  };

  // Groups every model by its filePath's directory instead of folderId.
  // Models with no filePath (shouldn't normally happen post-migration, but
  // defensive) or whose filePath sits directly in the flat pre-feature
  // upload location (no real subdirectory under it) land in a single
  // synthetic "Uploads" bucket rather than fabricating structure that was
  // never there -- see the spec's Non-goals.
  const fileTree = useMemo(() => {
    type FileNode = { id: string; label: string; children: FileNode[]; childMap: Record<string, FileNode> };
    const root: FileNode = { id: "__root__", label: "", children: [], childMap: {} };
    const realPaths = new Map<string, string>();

    models.forEach((m) => {
      if (!m.filePath) return;
      // Shared with App.tsx's filteredModels via fileViewSegments (types.ts)
      // so the tree's node ids and the grid's filter can never derive
      // different "meaningful segments" for the same file.
      const meaningfulSegments = fileViewSegments(m.filePath);

      if (meaningfulSegments.length === 0) {
        if (!root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID]) {
          const node: FileNode = { id: FILE_VIEW_UPLOADS_BUCKET_ID, label: "Uploads", children: [], childMap: {} };
          root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID] = node;
          root.children.push(node);
        }
        return;
      }

      // The synthetic id's segments are always a *suffix* of the file's real
      // (normalized, filename-dropped) directory segments -- fileViewSegments
      // only ever drops a leading prefix (nothing, or everything through a
      // literal "uploads" segment). So realSegments.length - meaningfulSegments.length
      // is the number of dropped leading segments, constant for this file, and
      // slicing rawSegments to dropped + depth reconstructs the real absolute
      // path up to any node on this file's chain.
      const rawSegments = m.filePath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      rawSegments.pop(); // drop filename, mirrors fileViewSegments
      const dropped = rawSegments.length - meaningfulSegments.length;

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment, index) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
          realPaths.set(idPath, rawSegments.slice(0, dropped + index + 1).join("/"));
        }
        cursor = cursor.childMap[segment];
      });
    });

    // Tracked (possibly empty) folders get the same node-creation treatment
    // as model-derived ones, but seeded from a raw folder path instead of a
    // model's filePath -- there's no filename to drop, so this uses
    // fileViewFolderSegments instead of fileViewSegments. A tracked folder
    // that already has models under it is a no-op here: childMap already
    // has every node on its chain from the walk above.
    trackedFolderPaths.forEach((trackedPath) => {
      const meaningfulSegments = fileViewFolderSegments(trackedPath);
      if (meaningfulSegments.length === 0) return;

      const rawSegments = trackedPath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      const dropped = rawSegments.length - meaningfulSegments.length;

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment, index) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
          realPaths.set(idPath, rawSegments.slice(0, dropped + index + 1).join("/"));
        }
        cursor = cursor.childMap[segment];
      });
    });

    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => ({
      id: node.id,
      label: node.label,
      children: node.children.map(strip),
    });
    return { items: root.children.map(strip), realPaths };
  }, [models, trackedFolderPaths]);

  const [folderContextMenu, setFolderContextMenu] = useState<{
    mouseX: number;
    mouseY: number;
    nodeId: string;
    realPath: string | null;
  } | null>(null);

  // The Uploads bucket isn't a real single directory, so it gets a menu
  // with only New Folder (realPath: null, meaning "create at the library
  // root" -- see handleCreateFileViewFolder's parentPath contract). Every
  // other node keeps the full Rename/Move/Delete/New Folder menu, gated on
  // having a resolvable real path.
  const handleFileTreeContextMenu = (e: React.MouseEvent, nodeId: string) => {
    const isUploadsBucket = nodeId === FILE_VIEW_UPLOADS_BUCKET_ID;
    const realPath = isUploadsBucket ? null : fileTree.realPaths.get(nodeId) ?? null;
    if (!isUploadsBucket && !realPath) return;
    e.preventDefault();
    e.stopPropagation();
    setFolderContextMenu({ mouseX: e.clientX - 2, mouseY: e.clientY - 4, nodeId, realPath });
  };

  // If the node being mutated is the one currently selected (or an ancestor
  // of it), its synthetic id no longer resolves to anything after the
  // rename/delete -- App.tsx's positional segment match in filteredModels
  // would silently return zero models with no error. Falling back to "all"
  // mirrors the same defensive reset onViewModeChange already does when
  // switching modes ("avoid a stale id from one mode being misread as the
  // other mode's id").
  const resetSelectionIfMutated = (nodeId: string) => {
    if (currentFolderId === nodeId || currentFolderId.startsWith(`${nodeId}/`)) {
      onSelectFolder("all");
    }
  };

  const handleRenameFileViewFolder = async (nodeId: string, realPath: string) => {
    const currentName = realPath.split("/").pop() || "";
    const newName = window.prompt("Rename folder:", currentName);
    if (!newName || newName === currentName) return;
    try {
      await api.renameFileViewFolder(realPath, newName);
      resetSelectionIfMutated(nodeId);
      onFileViewMutated();
    } catch (err) {
      console.error("Folder rename failed:", err);
      alert(err instanceof Error ? err.message : "Folder rename failed");
    }
  };

  const handleDeleteFileViewFolder = async (nodeId: string, realPath: string) => {
    const count = models.filter((m) => {
      if (!m.filePath) return false;
      const norm = m.filePath.replace(/\\/g, "/");
      return norm === realPath || norm.startsWith(`${realPath}/`);
    }).length;
    const label = count === 1 ? "1 file" : `${count} files`;
    if (!window.confirm(`Delete this folder and everything in it (${label})? This cannot be undone.`)) return;
    try {
      const result = await api.deleteFileViewFolder(realPath);
      resetSelectionIfMutated(nodeId);
      onFileViewMutated();
      // The backend deletes the library entries first, then rmtree's the
      // directory. If that second half fails (typically a file held open by
      // another process on Windows), it reports directoryRemoved: false rather
      // than silently claiming success -- so surface that distinction instead
      // of leaving the user to discover the surviving folder themselves.
      if (result.directoryRemoved === false) {
        alert(
          `Removed ${result.deletedModels} library ${result.deletedModels === 1 ? "entry" : "entries"}, ` +
            `but the folder itself could not be fully deleted from disk:\n\n${result.path}\n\n` +
            `Something in it is probably open in another program. You may need to delete it manually.`,
        );
      }
    } catch (err) {
      console.error("Folder delete failed:", err);
      alert(err instanceof Error ? err.message : "Folder delete failed");
    }
  };

  const handleCreateFileViewFolder = async (parentPath: string | null) => {
    const name = window.prompt("New folder name:");
    if (!name) return;
    try {
      await api.createFileViewFolder(parentPath, name);
      onFileViewMutated();
    } catch (err) {
      console.error("Folder creation failed:", err);
      alert(err instanceof Error ? err.message : "Folder creation failed");
    }
  };

  const handleFileTreeDrop = async (e: React.DragEvent, nodeId: string) => {
    // preventDefault/stopPropagation unconditionally, before the payload
    // guard below -- onDragOver already preventDefaults for every non-
    // Uploads node regardless of payload, so this node is a valid drop
    // target for ANY drag, including an OS file drag from the desktop. If
    // that guard ran first and returned early on a non-file-view payload,
    // an unprevented native file drop would fall through to the browser's
    // default behavior (navigate the window to the dropped file).
    e.preventDefault();
    e.stopPropagation();
    const modelId = e.dataTransfer.getData("application/x-fileview-model");
    if (!modelId || nodeId === FILE_VIEW_UPLOADS_BUCKET_ID) return;
    const targetDir = fileTree.realPaths.get(nodeId);
    if (!targetDir) return;
    const model = models.find((m) => m.id === modelId);
    if (!model || !model.filePath) return;
    const filename = model.filePath.split(/[\\/]/).pop() || model.name;
    const isCopy = e.ctrlKey;
    try {
      if (isCopy) {
        // duplicateModel has no notion of a drop target: for a copy-mode
        // source it lands the copy beside the original (the same directory
        // the source file actually lives in), and for a reference-mode
        // source -- which lives outside the managed library, so "beside the
        // original" isn't a legal destination for a copy -- it lands under
        // UPLOAD_DIR mirroring the source's logical folder chain. A
        // drag-and-drop copy DID specify a destination (wherever the user
        // dropped it), so a second step relocates the freshly-created
        // duplicate into targetDir, reusing the same updateModelLocation
        // call the move branch already uses. The duplicate's own filePath
        // is used for its filename (not the original's `filename` var)
        // since duplicateModel gives the copy a fresh UUID-based filename.
        const duplicated = await api.duplicateModel(modelId);
        const duplicatedFilename =
          duplicated.filePath?.split(/[\\/]/).pop() || duplicated.name;
        try {
          await api.updateModelLocation(
            duplicated.id,
            `${targetDir}/${duplicatedFilename}`,
          );
        } catch (relocateErr) {
          // The duplicate already exists as a real row + real file at this
          // point, but it never reached the folder the user dropped it on.
          // Leaving it would strand a copy somewhere the user never asked
          // for while they only see an error -- e.g. Ctrl+dragging onto a
          // watch-folder-rooted node, which validate_destination always
          // rejects because a copy-mode file may not live in a watch folder.
          // Cleanup is best-effort and deliberately swallows its own failure
          // so the ORIGINAL relocate error is what surfaces to the user.
          try {
            await api.deleteModel(duplicated.id, true);
          } catch (cleanupErr) {
            console.error(
              "Failed to clean up orphaned duplicate after a failed copy-to-folder:",
              cleanupErr,
            );
          }
          throw relocateErr;
        }
      } else {
        await api.updateModelLocation(modelId, `${targetDir}/${filename}`);
      }
      onFileViewMutated();
    } catch (err) {
      console.error("File view drag operation failed:", err);
      alert(err instanceof Error ? err.message : "Operation failed");
    }
  };

  // forwardRef mirrors CustomTreeItem's shape above -- RichTreeView's
  // slots.item passes a ref through for focus/keyboard-nav/scroll-into-view;
  // a bare function component here would silently drop it (React 18)
  // instead of forwarding it to the underlying TreeItem.
  //
  // Also wrapped in useCallback keyed on [fileTree, models] (per review):
  // without memoization the component gets a new identity on every Sidebar
  // re-render -- including the very re-render triggered by
  // setFolderContextMenu when the user right-clicks -- which makes MUI
  // remount tree items instead of updating them in place, partially
  // undercutting the forwardRef fix above (remounted items lose focus/DOM
  // identity anyway). The closure reads handleFileTreeContextMenu (which
  // reads fileTree.realPaths) and handleFileTreeDrop (which additionally
  // reads models), so [fileTree, models] is the correct dependency list.
  const FileViewTreeItem = React.useCallback(
    React.forwardRef(function FileViewTreeItem(
      props: TreeItemProps,
      ref: React.Ref<HTMLLIElement>,
    ) {
      return (
        <TreeItem
          {...props}
          ref={ref}
          onContextMenu={(e) => handleFileTreeContextMenu(e, props.itemId)}
          onDragOver={(e) => {
            if (props.itemId !== FILE_VIEW_UPLOADS_BUCKET_ID) e.preventDefault();
          }}
          onDrop={(e) => handleFileTreeDrop(e, props.itemId)}
        />
      );
    }),
    [fileTree, models],
  );

  interface CustomLabelProps extends UseTreeItemLabelSlotOwnProps {
    status: UseTreeItemStatus;
    onClick: React.MouseEventHandler<HTMLElement>;
    onPlusClick: React.MouseEventHandler<HTMLElement>;
  }

  function CustomLabel({
    children,
    status,
    onClick,
    onPlusClick,
    ...props
  }: CustomLabelProps) {
    return (
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        flexGrow={1}
        sx={{ minWidth: 0 }}
        {...props}
      >
        <Typography noWrap>{children}</Typography>
        <Stack direction="row">
          <IconButton
            onClick={onPlusClick}
            aria-label="select item"
            size="small"
            sx={{ color: "grey.300" }}
          >
            <PlusIcon />
          </IconButton>
          <IconButton
            onClick={onClick}
            aria-label="select item"
            size="small"
            edge="end"
            sx={{ color: "grey.300" }}
          >
            <Trash2 />
          </IconButton>
        </Stack>
      </Stack>
    );
  }

  const CustomTreeItem = React.forwardRef(function CustomTreeItem(
    props: TreeItemProps,
    ref: React.Ref<HTMLLIElement>,
  ) {
    const { interactions, status } = useTreeItemUtils({
      itemId: props.itemId,
      children: props.children,
    });

    const handleContentClick: UseTreeItemContentSlotOwnProps["onClick"] = (
      event,
    ) => {
      onSelectFolder(props.itemId);
    };
    const count = folderCounts[props.itemId] || 0;

    const handleIconButtonClick = (event: React.MouseEvent) => {
      event.stopPropagation();
      handleDeleteRequest(props.itemId, count);
    };

    const handlePlusClick = (event: React.MouseEvent) => {
      event.stopPropagation();
      setCreatingSubfolderId(props.itemId);
      setIsCreatingRoot(true);
      document.getElementById("folder-name-input").focus();
    };

    return (
      <TreeItem
        {...props}
        ref={ref}
        onDragOver={(e) => handleDragOver(e, props.itemId)}
        onDragLeave={handleDragLeave}
        onDrop={(e) => handleDrop(e, props.itemId)}
        className={
          props.itemId === dragTargetId
            ? "bg-white/10 rounded-md ring-2 ring-white"
            : ""
        }
        slots={{
          label: CustomLabel,
        }}
        slotProps={
          {
            label: {
              onClick: handleIconButtonClick,
              onPlusClick: handlePlusClick,
              status,
            },
            content: { onClick: handleContentClick },
          } as TreeItemSlotProps
        }
      />
    );
  });

  return (
    <Container
      disableGutters
      sx={{ bgcolor: "common.black" }}
      className="border-r border-vault-700 flex flex-col h-full select-none relative shrink-0 group/sidebar mr-6"
      style={isDesktopVariant ? { width } : undefined}
      onDragLeave={() => setDragTargetId(null)}
    >
      <div className="p-6 flex items-center gap-3">
        <Stack
          direction="row"
          gap={1}
          sx={{
            justifyContent: "flex-start",
            alignItems: "baseline",
            minWidth: 0,
          }}
        >
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center shadow-lg shadow-blue-900/20 shrink-0 pt-1">
            <Box className="w-5 h-5 text-white pb-1" />
          </div>
          <Typography noWrap variant="h4">
            3D Vaultkeeper
          </Typography>
          <Typography
            noWrap
            variant="subtitle2"
            sx={{ color: "text.secondary" }}
          >
            v{APP_TAG}
          </Typography>
        </Stack>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 space-y-0.5 scrollbar-thin scrollbar-thumb-vault-700 scrollbar-track-transparent overflow-y-scroll">
        {viewMode === "logical" && (
          <>
            <div className="px-4 mb-4">
              <Button
                fullWidth
                startIcon={<Plus />}
                onClick={() => {
                  setIsCreatingRoot(true);
                  document.getElementById("folder-name-input").focus();
                }}
                variant="outlined"
              >
                New Root Folder
              </Button>
            </div>

            <form
              onSubmit={handleCreateFolderSubmit}
              className={`px-4 mb-4 transition-all duration-400 ${
                isCreatingRoot ? "opacity-100" : "opacity-0 origin-top h-0"
              }`}
            >
              <div className="flex items-center gap-1 mb-3">
                <OutlinedInput
                  id="folder-name-input"
                  type="text"
                  className="w-full"
                  placeholder="Folder Name..."
                  value={newRootName}
                  onChange={(e) => setNewRootName(e.target.value)}
                  onBlur={() => {
                    !newRootName.trim();
                    setIsCreatingRoot(false);
                    setCreatingSubfolderId("");
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      setIsCreatingRoot(false);
                      setCreatingSubfolderId("");
                    }
                  }}
                />
              </div>
            </form>
          </>
        )}

        <Button
          variant="contained"
          startIcon={<LayoutGrid />}
          color={currentFolderId === "all" ? "info" : "primary"}
          onClick={() => onSelectFolder("all")}
          endIcon={
            <Badge badgeContent={models.length} className="mr-2"></Badge>
          }
          className="w-full"
          sx={{ alignItems: "center", justifyContent: "space-between" }}
        >
          All Models
        </Button>

        <div className="pt-2 pb-1 px-3 text-xs font-semibold text-slate-500 uppercase tracking-wider flex justify-between items-center">
          <Typography variant="subtitle1">Library</Typography>
        </div>

        <div className="space-y-1 pb-4 ">
          <div className="flex gap-1 px-1 mb-2">
            <button
              onClick={() => onViewModeChange?.("logical")}
              className={`flex-1 text-xs py-1 rounded ${viewMode === "logical" ? "bg-blue-600 text-white" : "bg-vault-800 text-slate-400"}`}
            >
              Logical
            </button>
            <button
              onClick={() => onViewModeChange?.("file")}
              className={`flex-1 text-xs py-1 rounded ${viewMode === "file" ? "bg-blue-600 text-white" : "bg-vault-800 text-slate-400"}`}
            >
              File
            </button>
          </div>
          {viewMode === "file" && (
            <button
              onClick={() => handleCreateFileViewFolder(null)}
              className="w-full text-xs py-1 mb-2 rounded bg-vault-800 text-slate-300 hover:bg-vault-700"
            >
              + New Folder
            </button>
          )}
          {viewMode === "logical" ? (
            <RichTreeView
              items={treefolders()}
              slots={{ item: CustomTreeItem }}
              expansionTrigger="iconContainer"
              expandedItems={Array.from(expandedIds)}
              onItemExpansionToggle={handleItemExpansionToggle}
              isItemEditable
              onItemLabelChange={(itemId, label) => onRenameFolder(itemId, label)}
            />
          ) : (
            <RichTreeView
              items={fileTree.items}
              selectedItems={currentFolderId}
              slots={{ item: FileViewTreeItem }}
              onSelectedItemsChange={(_e, itemId) => {
                if (itemId) onSelectFolder(itemId as string);
              }}
            />
          )}
        </div>
      </nav>

      <Menu
        open={folderContextMenu !== null}
        onClose={() => setFolderContextMenu(null)}
        anchorReference="anchorPosition"
        anchorPosition={
          folderContextMenu ? { top: folderContextMenu.mouseY, left: folderContextMenu.mouseX } : undefined
        }
      >
        <MenuItem
          onClick={() => {
            if (folderContextMenu) handleCreateFileViewFolder(folderContextMenu.realPath);
            setFolderContextMenu(null);
          }}
        >
          New Folder
        </MenuItem>
        {folderContextMenu !== null && folderContextMenu.realPath !== null && [
          <MenuItem
            key="rename"
            onClick={() => {
              if (folderContextMenu?.realPath) handleRenameFileViewFolder(folderContextMenu.nodeId, folderContextMenu.realPath);
              setFolderContextMenu(null);
            }}
          >
            Rename
          </MenuItem>,
          <MenuItem
            key="move"
            onClick={() => {
              if (folderContextMenu?.realPath) {
                const targetParent = window.prompt(
                  "Move to which real folder path? (paste the full destination directory)",
                  folderContextMenu.realPath,
                );
                if (targetParent && targetParent !== folderContextMenu.realPath) {
                  api
                    .moveFileViewFolder(folderContextMenu.realPath, targetParent)
                    .then(onFileViewMutated)
                    .catch((err) => {
                      console.error("Folder move failed:", err);
                      alert(err instanceof Error ? err.message : "Folder move failed");
                    });
                }
              }
              setFolderContextMenu(null);
            }}
          >
            Move
          </MenuItem>,
          <MenuItem
            key="delete"
            onClick={() => {
              if (folderContextMenu?.realPath) handleDeleteFileViewFolder(folderContextMenu.nodeId, folderContextMenu.realPath);
              setFolderContextMenu(null);
            }}
          >
            Delete
          </MenuItem>,
        ]}
      </Menu>

      <div className="p-4 border-t border-vault-700 z-10 gap-3 flex flex-col">
        <Button
          variant="outlined"
          startIcon={<Settings />}
          color="info"
          onClick={onOpenSettings}
          className="w-full"
          sx={{ alignItems: "center", justifyContent: "center" }}
        >
          Settings
        </Button>

        <div className="bg-gradient-to-r from-blue-600 to-indigo-600 rounded-md p-3 shadow-lg">
          <p className="text-xs text-white/80 font-medium mb-1 truncate mb-2">
            Storage Used
          </p>
          <div className="w-full bg-black/20 rounded-full h-1.5 mb-1 overflow-hidden">
            <div
              className="bg-white h-full rounded-full transition-all duration-500 ease-out"
              style={{ width: `${percentUsed}%` }}
            ></div>
          </div>
          <p className="text-[10px] text-white/60 flex justify-between">
            <span>{formatSize(storageStats.used)}</span>
            <span>{formatSize(storageStats.total)}</span>
          </p>
        </div>
      </div>

      {/* Resizer Handle */}
      {isDesktopVariant && (
        <div
          className={`absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-blue-500 transition-colors z-50 ${
            isResizing ? "bg-blue-500" : "bg-transparent"
          }`}
          onMouseDown={startResizing}
        />
      )}
    </Container>
  );
};

export default Sidebar;
