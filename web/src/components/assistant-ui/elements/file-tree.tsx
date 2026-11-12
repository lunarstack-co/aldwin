"use client";

import { useState, type ComponentProps } from "react";
import { ChevronDownIcon, ChevronRightIcon, FileIcon, FolderIcon } from "lucide-react";
import { cn } from "@/utils/cn";
import { mono, paper } from "./surfaces";
import { take } from "../utils/range";

export interface FileTreeNode {
  path: string;
  name: string;
  depth: number;
  kind: "folder" | "file";
  additions?: number;
  deletions?: number;
}

export function FileTree({
  nodes,
  visibleCount,
  totalAdditions,
  totalDeletions,
  title,
  footer,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "nodes" | "visibleCount" | "totalAdditions" | "totalDeletions"
> & {
  nodes: readonly FileTreeNode[];
  visibleCount: number;
  totalAdditions: number;
  totalDeletions: number;
  title?: string | undefined;
  footer?: string | undefined;
}) {
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(() => new Set());
  const files = nodes.filter((node) => node.kind === "file").length;
  const hasStats = totalAdditions > 0 || totalDeletions > 0;

  const toggleFolder = (folderPath: string) => {
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) next.delete(folderPath);
      else next.add(folderPath);
      return next;
    });
  };

  const visibleNodes = take(nodes, visibleCount).filter((node) => {
    for (const prefix of collapsedFolders) {
      if (node.path !== prefix && node.path.startsWith(`${prefix}/`)) {
        return false;
      }
    }
    return true;
  });

  return (
    <div
      data-slot="file-tree"
      className={cn(
        paper,
        "flex w-full max-w-md flex-col gap-2 rounded-2xl p-3.5",
        className,
      )}
      {...props}
    >
      <div className="flex items-baseline justify-between gap-2 px-1">
        <span className="min-w-0 truncate text-[13.5px] font-medium">
          {title ?? `${files} files`}
        </span>
        {hasStats ? (
          <span className={cn(mono, "shrink-0 tabular-nums")}>
            <span className="text-emerald-700 dark:text-emerald-400">
              +{totalAdditions}
            </span>{" "}
            <span className="text-red-600 dark:text-red-400">
              −{totalDeletions}
            </span>
          </span>
        ) : null}
      </div>

      <div className="flex flex-col">
        {visibleNodes.map((node) => {
          const isCollapsed = node.kind === "folder" && collapsedFolders.has(node.path);
          if (node.kind === "folder") {
            return (
              <button
                key={node.path}
                type="button"
                aria-expanded={!isCollapsed}
                onClick={() => toggleFolder(node.path)}
                className="hover:bg-foreground/[0.03] flex w-full cursor-pointer items-center gap-2 rounded-lg px-1 py-1 text-start text-[13px] transition-colors"
                style={{ paddingInlineStart: `${0.25 + node.depth * 0.85}rem` }}
              >
                {isCollapsed ? (
                  <ChevronRightIcon aria-hidden className="text-muted-foreground size-3 shrink-0" />
                ) : (
                  <ChevronDownIcon aria-hidden className="text-muted-foreground size-3 shrink-0" />
                )}
                <FolderIcon aria-hidden className="text-muted-foreground size-3.5 shrink-0" />
                <span className="text-foreground/80 min-w-0 flex-1 truncate">
                  {node.name}
                </span>
              </button>
            );
          }

          return (
            <div
              key={node.path}
              className="hover:bg-foreground/[0.03] flex items-center gap-2 rounded-lg px-1 py-1 text-[13px] transition-colors"
              style={{ paddingInlineStart: `${0.25 + node.depth * 0.85}rem` }}
            >
              <FileIcon aria-hidden className="text-muted-foreground ms-3 size-3.5 shrink-0" />
              <span className="text-foreground/85 min-w-0 flex-1 truncate">
                {node.name}
              </span>
              {node.additions || node.deletions ? (
                <span className={cn(mono, "shrink-0 tabular-nums")}>
                  {node.additions ? (
                    <span className="text-emerald-700 dark:text-emerald-400">
                      +{node.additions}
                    </span>
                  ) : null}{" "}
                  {node.deletions ? (
                    <span className="text-red-600 dark:text-red-400">
                      −{node.deletions}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      {footer ? (
        <p className={cn(mono, "text-muted-foreground px-1")}>{footer}</p>
      ) : null}
    </div>
  );
}
