// frontend/hooks/useFolderTree.ts
import { useMemo } from "react";
import { TreeViewDefaultItemModelProperties } from "@mui/x-tree-view/models";
import {
  Folder,
  STLModel,
  FILE_VIEW_UPLOADS_BUCKET_ID,
  fileViewSegments,
  fileViewFolderSegments,
} from "../types";

export interface FolderTree {
  items: TreeViewDefaultItemModelProperties[];
  realPaths?: Map<string, string>;
}

// Builds the folder tree RichTreeView renders, in either view mode. Lifted
// verbatim from Sidebar.tsx's former treefolders()/fileTree so the sidebar
// and the FolderPicker dialog read from one source of truth instead of two
// trees that could silently drift apart.
export function useFolderTree(
  viewMode: "logical" | "file",
  folders: Folder[],
  models: STLModel[],
  trackedFolderPaths: string[],
): FolderTree {
  return useMemo(() => {
    if (viewMode === "logical") {
      const rootFolders = folders.filter((f) => f.parentId === null);
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
      return { items: treeitems };
    }

    // File view: group every model by its filePath's directory instead of
    // folderId. Models with no filePath, or whose filePath sits directly in
    // the flat pre-feature upload location, land in a single synthetic
    // "Uploads" bucket rather than fabricating structure that was never there.
    type FileNode = { id: string; label: string; children: FileNode[]; childMap: Record<string, FileNode> };
    const root: FileNode = { id: "__root__", label: "", children: [], childMap: {} };
    const realPaths = new Map<string, string>();

    models.forEach((m) => {
      if (!m.filePath) return;
      const meaningfulSegments = fileViewSegments(m.filePath);

      if (meaningfulSegments.length === 0) {
        if (!root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID]) {
          const node: FileNode = { id: FILE_VIEW_UPLOADS_BUCKET_ID, label: "Uploads", children: [], childMap: {} };
          root.childMap[FILE_VIEW_UPLOADS_BUCKET_ID] = node;
          root.children.push(node);
        }
        return;
      }

      const rawSegments = m.filePath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      rawSegments.pop();
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

    trackedFolderPaths.forEach((trackedPath) => {
      const rawSegments = trackedPath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      let meaningfulSegments = fileViewFolderSegments(trackedPath);
      if (meaningfulSegments.length === 0) meaningfulSegments = rawSegments;

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
  }, [viewMode, folders, models, trackedFolderPaths]);
}
