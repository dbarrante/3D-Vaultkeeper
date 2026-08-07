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
  nodesById: Map<string, TreeViewDefaultItemModelProperties>;
}

// Preserves a POSIX-absolute path's leading slash through the
// split("/").filter(s => s.length > 0).join("/") round-trip used below to
// rebuild realPaths, which otherwise silently drops it: "/app/uploads/Foo"
// splits to ["", "app", "uploads", "Foo"], and filtering out empty segments
// strips the leading "" -- so the rejoined path becomes "app/uploads/Foo",
// now relative. That's a real bug in this app's Linux Docker deployment: a
// relative destination is correctly rejected by ensure_unambiguous_path with
// a 400, so any File-view destination picked from this tree would fail
// there. Windows-style paths (e.g. "C:\...") must keep reconstructing
// without a leading slash exactly as before, so this only ever prefixes a
// path that was genuinely POSIX-absolute to begin with. A UNC path
// (\\server\share\...) normalizes to a leading "//", which the startsWith("//")
// check below matches first and correctly preserves as a double slash --
// a real UNC root, not the single-slash form.
function posixAbsolutePrefix(rawPath: string): string {
  const normalized = rawPath.replace(/\\/g, "/");
  if (normalized.startsWith("//")) return "//";
  if (normalized.startsWith("/")) return "/";
  return "";
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
      // Index children by parentId once, then recurse from the root folders
      // to arbitrary depth. The old code only ever attached direct children
      // of root folders (a second, flat pass over `folders`), so anything
      // nested 3+ levels deep structurally could not appear in the tree. This
      // is a pure capability extension: root selection (parentId === null)
      // and the sort order at every level are unchanged from before.
      const childrenByParentId = new Map<string, Folder[]>();
      folders.forEach((f) => {
        if (f.parentId === null) return;
        const siblings = childrenByParentId.get(f.parentId) ?? [];
        siblings.push(f);
        childrenByParentId.set(f.parentId, siblings);
      });

      // Guards against a corrupt parentId cycle (e.g. a self-parented row, or
      // A -> B -> A) sending this into infinite recursion. Such a cycle can't
      // happen through this app's own folder-creation endpoints, but nothing
      // stops a manually edited DB row from creating one, and an infinite
      // recursion here would hang the whole Sidebar/FolderPicker render.
      const nodesById = new Map<string, TreeViewDefaultItemModelProperties>();
      const buildNode = (folder: Folder, visited: Set<string>): TreeViewDefaultItemModelProperties => {
        const nextVisited = new Set(visited).add(folder.id);
        const children = (childrenByParentId.get(folder.id) ?? [])
          .filter((child) => !nextVisited.has(child.id))
          .map((child) => buildNode(child, nextVisited))
          .sort((a, b) => a.label.localeCompare(b.label));
        const node = { id: folder.id, label: folder.name, children };
        nodesById.set(node.id, node);
        return node;
      };

      const treeitems = folders
        .filter((f) => f.parentId === null)
        .map((folder) => buildNode(folder, new Set()))
        .sort((a, b) => a.label.localeCompare(b.label));
      return { items: treeitems, nodesById };
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
      const absolutePrefix = posixAbsolutePrefix(m.filePath);

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment, index) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
          realPaths.set(idPath, absolutePrefix + rawSegments.slice(0, dropped + index + 1).join("/"));
        }
        cursor = cursor.childMap[segment];
      });
    });

    trackedFolderPaths.forEach((trackedPath) => {
      const rawSegments = trackedPath.replace(/\\/g, "/").split("/").filter((s) => s.length > 0);
      let meaningfulSegments = fileViewFolderSegments(trackedPath);
      if (meaningfulSegments.length === 0) meaningfulSegments = rawSegments;

      const dropped = rawSegments.length - meaningfulSegments.length;
      const absolutePrefix = posixAbsolutePrefix(trackedPath);

      let cursor = root;
      let idPath = "file";
      meaningfulSegments.forEach((segment, index) => {
        idPath += `/${segment}`;
        if (!cursor.childMap[segment]) {
          const node: FileNode = { id: idPath, label: segment, children: [], childMap: {} };
          cursor.childMap[segment] = node;
          cursor.children.push(node);
          realPaths.set(idPath, absolutePrefix + rawSegments.slice(0, dropped + index + 1).join("/"));
        }
        cursor = cursor.childMap[segment];
      });
    });

    const nodesById = new Map<string, TreeViewDefaultItemModelProperties>();
    const strip = (node: FileNode): TreeViewDefaultItemModelProperties => {
      const stripped = {
        id: node.id,
        label: node.label,
        children: node.children.map(strip),
      };
      nodesById.set(stripped.id, stripped);
      return stripped;
    };
    return { items: root.children.map(strip), realPaths, nodesById };
  }, [viewMode, folders, models, trackedFolderPaths]);
}
