"use client";

import { Button } from "@/components/atoms/button";
import { Mark } from "@/components/atoms/mark";
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
      <h1 className="text-h2 font-semibold">{copy.title}</h1>
      <p className="max-w-[480px] text-md text-fg-secondary">{copy.body}</p>
      <Button variant="primary" onClick={() => window.location.reload()}>
        {copy.reload}
      </Button>
    </div>
  );
}
