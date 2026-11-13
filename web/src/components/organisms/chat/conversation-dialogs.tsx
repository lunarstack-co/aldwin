"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import copy from "@/data/chat/conversation-menu.json";
import { useSession } from "@/providers/chat-session-provider";
import { useConversationActions, type ConversationTarget } from "@/state/conversation-actions-store";
import { fill, shorten } from "@/utils/text";

const MAX_TITLE = 100;

function RenameDialog({ target }: { target: ConversationTarget }) {
  const session = useSession();
  const close = useConversationActions((s) => s.close);
  const [title, setTitle] = useState(target.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cleaned = title.replace(/\s+/g, " ").trim();
  const canSave = cleaned !== "" && cleaned !== target.title && !busy;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSave) return;
    setBusy(true);
    const outcome = await session.renameConversation(target.id, cleaned);
    setBusy(false);
    if (outcome.ok) close();
    else setError(outcome.message);
  }

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent data-testid="rename-dialog">
        <form onSubmit={(event) => void onSubmit(event)} className="grid gap-4">
          <div className="grid gap-1.5 pe-6">
            <DialogTitle>{copy.renameTitle}</DialogTitle>
            <DialogDescription>{copy.renameHint}</DialogDescription>
          </div>
          <div className="grid gap-1.5">
            <Input
              aria-label={copy.renameLabel}
              value={title}
              maxLength={MAX_TITLE}
              autoFocus
              onFocus={(event) => event.currentTarget.select()}
              onChange={(event) => {
                setTitle(event.target.value);
                setError(null);
              }}
              aria-invalid={error !== null}
              data-testid="rename-input"
              className="max-md:h-10"
            />
            {error ? (
              <p role="alert" className="text-destructive text-sm">
                {error}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close} className="max-md:h-10">
              {copy.cancel}
            </Button>
            <Button type="submit" disabled={!canSave} data-testid="rename-save" className="max-md:h-10">
              {busy ? copy.saving : copy.save}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({ target }: { target: ConversationTarget }) {
  const session = useSession();
  const close = useConversationActions((s) => s.close);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onDelete() {
    setBusy(true);
    const outcome = await session.deleteConversation(target.id);
    setBusy(false);
    if (!outcome.ok) {
      setError(outcome.message);
      return;
    }
    close();
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && close()}>
      <DialogContent data-testid="delete-dialog" showCloseButton={false}>
        <DialogTitle>{copy.deleteTitle}</DialogTitle>
        <DialogDescription>{fill(copy.deleteBody, { title: shorten(target.title, 60) })}</DialogDescription>
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={close} disabled={busy} className="max-md:h-10">
            {copy.cancel}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => void onDelete()}
            disabled={busy}
            data-testid="delete-confirm"
            // The kit's tinted destructive style is too faint for the one button that deletes.
            className="bg-destructive hover:bg-destructive/90 dark:bg-destructive dark:hover:bg-destructive/90 dark:text-background text-white max-md:h-10"
          >
            {busy ? copy.deleting : copy.deleteConfirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Keyed by conversation so each dialog starts fresh. */
export function ConversationDialogs() {
  const renaming = useConversationActions((s) => s.renaming);
  const deleting = useConversationActions((s) => s.deleting);
  return (
    <>
      {renaming ? <RenameDialog key={`rename-${renaming.id}`} target={renaming} /> : null}
      {deleting ? <DeleteDialog key={`delete-${deleting.id}`} target={deleting} /> : null}
    </>
  );
}
