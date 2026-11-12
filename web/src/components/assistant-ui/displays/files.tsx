"use client";

import { FileTree, type FileTreeNode } from "@/components/assistant-ui/elements/file-tree";
import copy from "@/data/chat/displays.json";
import type { FilesDisplay } from "@/types/protocol";
import { fill } from "@/utils/text";

function buildTreeNodes(display: FilesDisplay): FileTreeNode[] {
  const seenFolders = new Set<string>();
  const nodes: FileTreeNode[] = [];

  for (const entry of display.entries) {
    const clean = entry.path.replace(/^\/+|\/+$/g, "");
    if (!clean) continue;
    const parts = clean.split("/");
    for (let i = 0; i < parts.length - 1; i += 1) {
      const folderPath = parts.slice(0, i + 1).join("/");
      if (!seenFolders.has(folderPath)) {
        seenFolders.add(folderPath);
        nodes.push({
          path: folderPath,
          name: parts[i] ?? folderPath,
          depth: i,
          kind: "folder",
        });
      }
    }
    if (entry.kind === "folder") {
      if (!seenFolders.has(clean)) {
        seenFolders.add(clean);
        nodes.push({
          path: clean,
          name: parts[parts.length - 1] ?? clean,
          depth: parts.length - 1,
          kind: "folder",
        });
      }
    } else {
      nodes.push({
        path: clean,
        name: parts[parts.length - 1] ?? clean,
        depth: parts.length - 1,
        kind: "file",
        ...(entry.additions != null ? { additions: entry.additions } : {}),
        ...(entry.deletions != null ? { deletions: entry.deletions } : {}),
      });
    }
  }
  return nodes;
}

export function FilesDisplayView({ display }: { display: FilesDisplay }) {
  const nodes = buildTreeNodes(display);
  const count = display.entries.length;
  const totalAdditions = display.entries.reduce((sum, e) => sum + (e.additions ?? 0), 0);
  const totalDeletions = display.entries.reduce((sum, e) => sum + (e.deletions ?? 0), 0);
  const title =
    count === 0
      ? `${display.root} · ${copy.files.empty}`
      : fill(count === 1 ? copy.files.headerOne : copy.files.header, {
          root: display.root,
          count,
        });

  return (
    <FileTree
      data-testid="display-files"
      nodes={nodes}
      visibleCount={nodes.length}
      totalAdditions={totalAdditions}
      totalDeletions={totalDeletions}
      title={title}
      footer={display.truncated ? fill(copy.truncated, { count }) : undefined}
      className="max-w-full"
    />
  );
}
