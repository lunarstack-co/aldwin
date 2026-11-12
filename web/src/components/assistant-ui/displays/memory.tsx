"use client";

import { MemoryChips } from "@/components/assistant-ui/elements/memory-chips";
import copy from "@/data/chat/displays.json";
import type { MemoryDisplay } from "@/types/protocol";

export function MemoryDisplayView({ display }: { display: MemoryDisplay }) {
  const chips = display.changes.map((item, idx) => ({
    id: `mem-${idx}`,
    text: item.text,
    change: item.change,
    label:
      item.change === "added"
        ? copy.memory.added
        : item.change === "replaced"
          ? copy.memory.replaced
          : copy.memory.removed,
  }));

  return (
    <MemoryChips
      data-testid="display-memory"
      headerLabel={copy.memory.header}
      chips={chips}
      className="max-w-full"
    />
  );
}
