"use client";

import { Mark } from "@/components/brand/mark";
import { Button } from "@/components/ui/button";
import copy from "@/data/chat/stopped.json";

/** The end of the line: Aldwin is gone and a reload is the only way back. */
export function StoppedScreen() {
  return (
    <div
      role="alert"
      data-testid="aldwin-stopped"
      className="flex flex-1 flex-col items-center justify-center gap-4 px-4 text-center"
    >
      <Mark className="size-10" />
      <h1 className="text-2xl">{copy.title}</h1>
      <p className="text-muted-foreground max-w-[480px] text-base">{copy.body}</p>
      <Button onClick={() => window.location.reload()} className="max-md:h-10">
        {copy.reload}
      </Button>
    </div>
  );
}
