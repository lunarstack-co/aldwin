"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import copy from "@/data/chat/take-over.json";

/** Another tab holds the socket; this one can take it. Nothing else here works until then. */
export function TakeOverDialog({ onTakeOver }: { onTakeOver: () => void }) {
  return (
    <Dialog open disablePointerDismissal>
      <DialogContent
        showCloseButton={false}
        data-testid="take-over"
        initialFocus={() => document.querySelector<HTMLElement>("[data-testid=take-over-button]")}
        className="max-md:top-auto max-md:bottom-0 max-md:max-w-full max-md:translate-y-0 max-md:rounded-b-none"
      >
        <DialogTitle>{copy.title}</DialogTitle>
        <DialogDescription>{copy.body}</DialogDescription>
        <DialogFooter>
          <Button onClick={onTakeOver} data-testid="take-over-button" className="max-md:h-10">
            {copy.takeOver}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
