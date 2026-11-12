"use client";

import { ComposerPrimitive, type TriggerAdapter } from "@assistant-ui/react";
import { AtSign, Settings } from "lucide-react";
import { useEffect, useMemo, type MouseEvent } from "react";

import { ComposerTriggerPopover } from "@/components/assistant-ui/elements/composer-trigger-popover.aui";
import { Button } from "@/components/ui/button";
import copy from "@/data/chat/skills-menu.json";
import { useChatStore } from "@/state/chat-store";
import { searchSkills, skillFormatter, toTriggerItem, visibleSkills } from "@/utils/skills";

// Buttons by the menu must not take focus from the composer, or the menu would lose its cursor.
const keepFocus = (event: MouseEvent) => event.preventDefault();

/** Closes the menu, keeping the text, when the pointer goes down anywhere outside the composer. */
function CloseOnOutsidePointer() {
  const popover = ComposerPrimitive.unstable_useTriggerPopoverScopeContext();
  useEffect(() => {
    function onPointerDown(event: PointerEvent) {
      if (event.target instanceof Element && event.target.closest("[data-skills-scope]")) return;
      popover.close();
    }
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [popover]);
  return null;
}

/** The `@` menu: skills only, inserted as `@name`, with the catalogue's loading, empty and error states. */
export function SkillsPopover() {
  const skills = useChatStore((s) => s.skills);
  const setStatus = useChatStore((s) => s.setSkillsStatus);

  // No categories, so the menu opens straight on the list and filters it as the user types.
  const adapter = useMemo<TriggerAdapter>(
    () => ({
      categories: () => [],
      categoryItems: () => [],
      search: (query) => (skills.status === "ready" ? searchSkills(skills.items, query).map(toTriggerItem) : []),
    }),
    [skills],
  );

  const failed = skills.status === "error";
  const empty = failed ? copy.error : visibleSkills(skills.items).length === 0 ? copy.empty : copy.noMatch;

  // A listbox may hold only options, so the buttons sit beside it; the frame shows only while the list is open.
  return (
    <div
      data-testid="skills-menu"
      className="bg-popover text-popover-foreground absolute start-0 bottom-full z-50 mb-2 hidden w-[min(20rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border shadow-md has-[[role=listbox]]:flex"
    >
      <ComposerTriggerPopover
        char="@"
        adapter={adapter}
        directive={{ formatter: skillFormatter }}
        fallbackIcon={AtSign}
        isLoading={skills.status === "idle" || skills.status === "loading"}
        loadingLabel={copy.loading}
        emptyItemsLabel={empty}
        aria-label={copy.label}
        className="static m-0 max-h-[min(20rem,45dvh)] w-full overflow-y-auto rounded-none border-0 max-md:[&_[role=option]]:min-h-10"
      >
        <CloseOnOutsidePointer />
      </ComposerTriggerPopover>
      <div className="flex flex-col gap-1 border-t p-1">
        {failed ? (
          <Button
            variant="outline"
            size="sm"
            onMouseDown={keepFocus}
            onClick={() => setStatus("idle")}
            data-testid="skills-retry"
            className="max-md:h-10"
          >
            {copy.retry}
          </Button>
        ) : null}
        {/* Skills are managed in Settings, which is not built yet; this stays disabled like the rail's Settings entry. */}
        <Button variant="ghost" size="sm" disabled className="w-full justify-start" data-testid="skills-manage">
          <Settings aria-hidden />
          {copy.manage}
        </Button>
      </div>
    </div>
  );
}
