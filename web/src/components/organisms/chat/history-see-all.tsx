"use client";

import { ArrowLeft, Search } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import copy from "@/data/chat/history-see-all.json";
import railCopy from "@/data/chat/rail.json";
import type { SessionMeta } from "@/types/protocol";
import { isTyping } from "@/utils/dom";
import { filterSessions, groupSessions } from "@/utils/history";
import { fill } from "@/utils/text";

import { HistoryList } from "./history-list";

export const SEARCH_DEBOUNCE_MS = 150;

export function HistorySeeAll({
  sessions,
  disabled,
  onBack,
}: {
  sessions: SessionMeta[];
  disabled: boolean;
  onBack: () => void;
}) {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  // Fixed when the view opens, so a row does not jump groups while it is read.
  const [now] = useState(() => new Date());
  const titleId = useId();
  const searchId = useId();
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const id = setTimeout(() => setQuery(input), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [input]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onBack();
      else if (event.key === "/" && !isTyping(event.target)) {
        event.preventDefault();
        search.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  const matches = new Set(filterSessions(sessions, query, railCopy.untitled));
  const groups = groupSessions(
    sessions.filter((s) => matches.has(s)),
    now,
  );
  const sections = [
    { id: "today", label: copy.today, items: groups.today },
    { id: "yesterday", label: copy.yesterday, items: groups.yesterday },
    { id: "previous7", label: copy.previous7, items: groups.previous7 },
    { id: "older", label: copy.older, items: groups.older },
  ].filter((section) => section.items.length > 0);

  return (
    <section aria-labelledby={titleId} data-testid="history-see-all" className="flex min-h-0 flex-1 flex-col">
      <header className="flex items-center gap-2 border-b px-4 py-2 sm:px-6">
        <Button
          variant="ghost"
          size="icon"
          onClick={onBack}
          aria-label={copy.back}
          title={copy.back}
          data-testid="see-all-back"
          className="max-md:size-10"
        >
          <ArrowLeft aria-hidden />
        </Button>
        <h1 id={titleId} className="text-sm font-medium">
          {copy.title}
        </h1>
      </header>
      <div className="mx-auto flex min-h-0 w-full max-w-(--thread-max-width) flex-1 flex-col gap-4 px-4 py-6 sm:px-6">
        <div className="relative">
          <label htmlFor={searchId} className="sr-only">
            {copy.searchLabel}
          </label>
          <Search aria-hidden className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
          <Input
            ref={search}
            id={searchId}
            type="search"
            autoFocus
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={copy.searchPlaceholder}
            data-testid="history-search"
            className="pr-10 pl-8 max-md:h-10"
          />
          <Kbd className="absolute top-1/2 right-2 -translate-y-1/2" title={copy.searchShortcut}>
            /
          </Kbd>
        </div>
        <p role="status" className="sr-only">
          {fill(copy.resultCount, { count: matches.size })}
        </p>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {sections.length === 0 ? (
            <p className="text-muted-foreground px-2 py-1.5 text-sm" data-testid="history-empty">
              {sessions.length === 0 ? copy.empty : fill(copy.noMatch, { query: query.trim() })}
            </p>
          ) : (
            sections.map((section) => (
              <div key={section.id} className="mb-6 flex flex-col gap-1" data-testid={`history-group-${section.id}`}>
                {/* Dates read as quiet labels, apart from the titles under them. */}
                <h2 className="text-muted-foreground px-2 pb-1 text-[11px] font-medium tracking-wider uppercase">
                  {section.label}
                </h2>
                <HistoryList
                  ids={section.items.map((s) => s.id)}
                  disabled={disabled}
                  testId="see-all-row"
                  tone="page"
                />
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
