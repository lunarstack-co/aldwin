"use client";

import dynamic from "next/dynamic";

import type { Display } from "@/types/protocol";

// Started when a tool call begins, so a display is ready by the time its result lands.
let preloaded = false;
export function preloadDisplays(): void {
  if (preloaded) return;
  preloaded = true;
  void Promise.all([
    import("./terminal"),
    import("./diff"),
    import("./files"),
    import("./search"),
    import("./page"),
    import("./table"),
    import("./memory"),
    import("./denied"),
  ]);
}

/** Holds the display's space while its code loads, so the row does not jump. */
function DisplayPlaceholder() {
  return <div aria-hidden className="bg-muted/60 h-16 animate-pulse rounded-xl motion-reduce:animate-none" />;
}

const TerminalDisplayView = dynamic(
  () => import("./terminal").then((m) => m.TerminalDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const DiffDisplayView = dynamic(
  () => import("./diff").then((m) => m.DiffDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const FilesDisplayView = dynamic(
  () => import("./files").then((m) => m.FilesDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const SearchDisplayView = dynamic(
  () => import("./search").then((m) => m.SearchDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const PageDisplayView = dynamic(
  () => import("./page").then((m) => m.PageDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const TableDisplayView = dynamic(
  () => import("./table").then((m) => m.TableDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const MemoryDisplayView = dynamic(
  () => import("./memory").then((m) => m.MemoryDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);
const DeniedDisplayView = dynamic(
  () => import("./denied").then((m) => m.DeniedDisplayView),
  { ssr: false, loading: DisplayPlaceholder },
);

export function DisplayRenderer({
  display,
  pending = false,
  maxCollapsedLines = 20,
}: {
  display: Display;
  pending?: boolean;
  maxCollapsedLines?: number;
}) {
  switch (display.kind) {
    case "terminal":
      return <TerminalDisplayView display={display} pending={pending} />;
    case "diff":
      return <DiffDisplayView display={display} maxCollapsedLines={maxCollapsedLines} />;
    case "files":
      return <FilesDisplayView display={display} />;
    case "search":
      return <SearchDisplayView display={display} />;
    case "page":
      return <PageDisplayView display={display} />;
    case "table":
      return <TableDisplayView display={display} />;
    case "memory":
      return <MemoryDisplayView display={display} />;
    case "denied":
      return <DeniedDisplayView display={display} />;
    case "grant":
      return null;
  }
}
