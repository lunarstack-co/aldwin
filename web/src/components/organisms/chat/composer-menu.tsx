"use client";

import { Settings } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/utils/cn";

export type MenuItem = { id: string; label: string; description?: string; icon: ReactNode };

export type MenuCopy = {
  label: string;
  search: string;
  loading: string;
  none: string;
  noMatch: string;
  error: string;
  retry: string;
  manage: string;
};

export type ComposerMenuProps = {
  testId: string;
  button: { label: string; icon: ReactNode; testId: string; disabled: boolean };
  status: "loading" | "error" | "ready";
  results: (query: string) => MenuItem[];
  copy: MenuCopy;
  manageHref: string;
  returnFocus: RefObject<HTMLElement | null>;
  onOpen: () => void;
  onPick: (id: string) => void;
  onRetry: () => void;
};

/** A button-opened list that writes nothing into the message until an item is picked. */
export function ComposerMenu({
  initialOpen = false,
  testId,
  button,
  status,
  results,
  copy,
  manageHref,
  returnFocus,
  onOpen,
  onPick,
  onRetry,
}: ComposerMenuProps & { initialOpen?: boolean }) {
  const listId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const items = status === "ready" ? results(query) : [];
  const current = Math.min(active, Math.max(items.length - 1, 0));

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (!next) return;
    setQuery("");
    setActive(0);
    onOpen();
  }

  // Loaded by a click, so that click opens it.
  const openOnMount = useRef(initialOpen);
  useEffect(() => {
    if (!openOnMount.current) return;
    openOnMount.current = false;
    onOpenChange(true);
  });

  function pick(id: string) {
    setOpen(false);
    onPick(id);
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (items.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + items.length) % items.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      pick(items[current].id);
    }
  }

  const message =
    status === "loading"
      ? copy.loading
      : status === "error"
        ? copy.error
        : results("").length === 0
          ? copy.none
          : items.length === 0
            ? copy.noMatch
            : null;

  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger
        disabled={button.disabled}
        render={
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={button.label}
            title={button.label}
            data-testid={button.testId}
            className="max-md:size-10"
          />
        }
      >
        {button.icon}
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={8}
        initialFocus={searchRef}
        finalFocus={returnFocus}
        data-testid={testId}
        className="w-[min(20rem,calc(100vw-2rem))] gap-0 overflow-hidden rounded-xl p-0"
      >
        <input
          ref={searchRef}
          type="text"
          role="combobox"
          aria-expanded
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={items.length > 0 ? `${listId}-${current}` : undefined}
          aria-label={copy.search}
          placeholder={copy.search}
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          className="placeholder:text-muted-foreground/60 border-b bg-transparent px-3 py-2 text-sm outline-none max-md:h-10"
        />
        <ul
          id={listId}
          role="listbox"
          aria-label={copy.label}
          className="max-h-[min(20rem,45dvh)] overflow-y-auto p-1 empty:hidden"
        >
          {items.map((item, index) => (
            <li
              key={item.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={index === current}
              data-menu-item={item.id}
              onMouseDown={(event) => event.preventDefault()}
              onMouseMove={() => setActive(index)}
              onClick={() => pick(item.id)}
              className="aria-selected:bg-accent aria-selected:text-accent-foreground flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-sm max-md:min-h-10 [&_svg]:size-4 [&_svg]:shrink-0"
            >
              {item.icon}
              <span className="flex min-w-0 flex-col">
                <span className="truncate font-medium">{item.label}</span>
                {item.description ? (
                  <span className="text-muted-foreground truncate text-xs">{item.description}</span>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        {message ? (
          <p role="status" className="text-muted-foreground px-3 py-2 text-sm">
            {message}
          </p>
        ) : null}
        <div className="flex flex-col gap-1 border-t p-1">
          {status === "error" ? (
            <Button variant="outline" size="sm" onClick={onRetry} data-testid={`${testId}-retry`} className="max-md:h-10">
              {copy.retry}
            </Button>
          ) : null}
          <Link
            href={manageHref}
            onClick={() => setOpen(false)}
            data-testid={`${testId}-manage`}
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-full justify-start max-md:h-10")}
          >
            <Settings aria-hidden />
            {copy.manage}
          </Link>
        </div>
      </PopoverContent>
    </Popover>
  );
}
