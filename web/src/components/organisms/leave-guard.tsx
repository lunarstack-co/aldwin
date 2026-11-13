"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import copy from "@/data/chat/leave.json";
import { useUnsavedStore } from "@/state/unsaved-store";

const SENTINEL = "aldwinUnsaved";

type Router = ReturnType<typeof useRouter>;

/** Replaces the guard's extra history entry when it is current, so one Back leaves the new page. */
export function leaveTo(router: Router, href: string): void {
  if (window.history.state?.[SENTINEL]) router.replace(href);
  else router.push(href);
}

function isPlainClick(event: MouseEvent): boolean {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/** Covers link clicks, Back and Forward, reload and tab close. Forms opt in with `useUnsavedChanges`. */
export function LeaveGuard() {
  const router = useRouter();
  const dirty = useUnsavedStore((s) => s.dirty.size > 0);
  const pending = useUnsavedStore((s) => s.pending);
  const answer = useUnsavedStore((s) => s.answer);
  // Back is caught with an extra history entry; the guard marks its own history steps to tell them from the user's.
  const ownStep = useRef<"off-entry" | "leave" | null>(null);

  // Always listening, because a step off the extra entry can land after the form that caused it is gone.
  useEffect(() => {
    const pushEntry = () =>
      window.history.pushState({ ...window.history.state, [SENTINEL]: true }, "", window.location.href);
    function onPopState() {
      const step = ownStep.current;
      ownStep.current = null;
      const dirtyNow = useUnsavedStore.getState().dirty.size > 0;
      if (step === "leave") return;
      if (step === "off-entry") {
        // Edits started again before the step landed: put the extra entry back.
        if (dirtyNow && !window.history.state?.[SENTINEL]) pushEntry();
        return;
      }
      if (!dirtyNow) return;
      // The user went back past the extra entry: stand on it again and ask.
      pushEntry();
      useUnsavedStore.getState().confirmLeave(() => {
        ownStep.current = "leave";
        window.history.go(-2);
      });
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    function onClick(event: MouseEvent) {
      if (!isPlainClick(event) || event.defaultPrevented) return;
      const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
      if (!(link instanceof HTMLAnchorElement) || link.target === "_blank" || link.hasAttribute("download")) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin !== window.location.origin || url.href === window.location.href) return;
      event.preventDefault();
      event.stopPropagation();
      useUnsavedStore.getState().confirmLeave(() => leaveTo(router, `${url.pathname}${url.search}${url.hash}`));
    }
    if (!window.history.state?.[SENTINEL] && ownStep.current !== "off-entry") {
      window.history.pushState({ ...window.history.state, [SENTINEL]: true }, "", window.location.href);
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      document.removeEventListener("click", onClick, true);
      // Saved or discarded in place: step off the extra entry so Back works in one press.
      if (window.history.state?.[SENTINEL] && !useUnsavedStore.getState().leaving) {
        ownStep.current = "off-entry";
        window.history.back();
      }
    };
  }, [dirty, router]);

  return (
    <Dialog open={pending !== null} onOpenChange={(open) => !open && answer(false)}>
      <DialogContent showCloseButton={false} data-testid="leave-dialog">
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription>{copy.body}</DialogDescription>
        <DialogFooter>
          <Button variant="outline" autoFocus onClick={() => answer(false)} className="max-md:h-10">
            {copy.stay}
          </Button>
          <Button onClick={() => answer(true)} data-testid="leave-confirm" className="max-md:h-10">
            {copy.leave}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
