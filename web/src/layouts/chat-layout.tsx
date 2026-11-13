"use client";

import { Menu, PanelLeft } from "lucide-react";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

import { Lockup } from "@/components/brand/lockup";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import copy from "@/data/chat/rail.json";
import { cn } from "@/utils/cn";
import { isTyping } from "@/utils/dom";

const COLLAPSED_KEY = "aldwin.sidebar-collapsed";

let collapsedNow: boolean | null = null;
const collapseListeners = new Set<() => void>();

function readCollapsed(): boolean {
  if (collapsedNow !== null) return collapsedNow;
  try {
    collapsedNow = window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    collapsedNow = false;
  }
  return collapsedNow;
}

function setCollapsedStored(collapsed: boolean): void {
  collapsedNow = collapsed;
  try {
    window.localStorage.setItem(COLLAPSED_KEY, collapsed ? "1" : "0");
  } catch {
    // Private windows may refuse storage; the choice then lasts for this page only.
  }
  collapseListeners.forEach((listener) => listener());
}

function subscribeCollapsed(listener: () => void): () => void {
  collapseListeners.add(listener);
  return () => collapseListeners.delete(listener);
}

export function ChatLayout({
  rail,
  railLabel,
  stageLabel,
  header,
  children,
}: {
  rail: (close: () => void) => ReactNode;
  railLabel: string;
  stageLabel: string;
  header?: ReactNode;
  children: ReactNode;
}) {
  const collapsed = useSyncExternalStore(subscribeCollapsed, readCollapsed, () => false);
  const [sheetOpen, setSheetOpen] = useState(false);
  // Only a toggle slides the rail; a page that loads collapsed starts collapsed.
  const [animate, setAnimate] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key.toLowerCase() !== "b" || !(event.metaKey || event.ctrlKey) || isTyping(event.target)) return;
      event.preventDefault();
      setAnimate(true);
      setCollapsedStored(!readCollapsed());
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggle = () => {
    setAnimate(true);
    setCollapsedStored(!readCollapsed());
  };

  return (
    <div data-testid="aldwin-chat-shell" className="flex min-h-0 flex-1">
      <nav
        aria-label={railLabel}
        data-testid="rail"
        data-collapsed={collapsed || undefined}
        className={cn(
          "bg-sidebar text-sidebar-foreground hidden w-[260px] shrink-0 overflow-hidden md:flex",
          // Visibility flips at the end of a close and the start of an open, so a closed rail takes no focus.
          animate && "transition-[width,visibility] duration-200 ease-out",
          collapsed && "md:invisible md:w-0",
        )}
      >
        <div className="border-sidebar-border flex w-[260px] shrink-0 flex-col border-r p-3">{rail(() => {})}</div>
      </nav>
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent side="left" className="bg-sidebar text-sidebar-foreground w-[280px] p-3 md:hidden" data-testid="rail-sheet">
          <SheetTitle className="sr-only">{railLabel}</SheetTitle>
          {rail(() => setSheetOpen(false))}
        </SheetContent>
      </Sheet>
      <section aria-label={stageLabel} className="relative flex min-w-0 flex-1 flex-col">
        <div className="flex min-h-12 items-center gap-2 border-b px-2 sm:px-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSheetOpen(true)}
            aria-label={copy.openMenu}
            title={copy.openMenu}
            data-testid="open-rail"
            className="size-10 md:hidden"
          >
            <Menu aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            aria-label={collapsed ? copy.expand : copy.collapse}
            title={collapsed ? copy.expand : copy.collapse}
            aria-pressed={!collapsed}
            data-testid="toggle-rail"
            className="hidden md:inline-flex"
          >
            <PanelLeft aria-hidden />
          </Button>
          <Lockup className="md:hidden" />
          <div className="flex min-w-0 flex-1 items-center justify-end gap-2">{header}</div>
        </div>
        {children}
      </section>
    </div>
  );
}
