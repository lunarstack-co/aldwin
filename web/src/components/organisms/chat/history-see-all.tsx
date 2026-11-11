"use client";

import { ArrowLeft, Search } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Button } from "@/components/atoms/button";
import { Input } from "@/components/atoms/input";
import copy from "@/data/chat/history-see-all.json";
import railCopy from "@/data/chat/rail.json";
import type { SessionMeta } from "@/types/protocol";
import { filterSessions, groupSessions } from "@/utils/history";
import { fill } from "@/utils/text";

import { HistoryRailList } from "./history-rail-list";

/** Every saved conversation, searchable, split into today and earlier. */
export function HistorySeeAll({
  sessions,
  currentId,
  disabled,
  onOpen,
  onBack,
}: {
  sessions: SessionMeta[];
  currentId: string | null;
  disabled: boolean;
  onOpen: (id: string) => void;
  onBack: () => void;
}) {
  const [query, setQuery] = useState("");
  // Fixed when the view opens, so a row does not jump groups while it is read.
  const [now] = useState(() => new Date());
  const titleId = useId();
  const searchId = useId();

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onBack();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  const matches = filterSessions(sessions, query, railCopy.untitled);
  const groups = groupSessions(matches, now);
  const sections = [
    { id: "today", label: copy.today, items: groups.today },
    { id: "earlier", label: copy.earlier, items: groups.earlier },
  ].filter((section) => section.items.length > 0);

  return (
    <section
      aria-labelledby={titleId}
      data-testid="history-see-all"
      className="flex min-h-0 flex-1 flex-col"
    >
      <header className="flex items-center gap-2 border-b border-border-subtle px-4 py-2 sm:px-6">
        <Button icon onClick={onBack} aria-label={copy.back} title={copy.back} data-testid="see-all-back">
          <ArrowLeft aria-hidden className="size-4" />
        </Button>
        <h1 id={titleId} className="text-ui font-medium">
          {copy.title}
        </h1>
      </header>
      <div className="mx-auto flex min-h-0 w-full max-w-[720px] flex-1 flex-col gap-4 px-4 py-6 sm:px-6">
        <div className="relative">
          <label htmlFor={searchId} className="sr-only">
            {copy.searchLabel}
          </label>
          <Search aria-hidden className="pointer-events-none absolute left-2.5 top-2 size-4 text-fg-tertiary" />
          <Input
            id={searchId}
            type="search"
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={copy.searchPlaceholder}
            data-testid="history-search"
            className="pl-8"
          />
        </div>
        <p role="status" className="sr-only">
          {fill(copy.resultCount, { count: matches.length })}
        </p>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {sections.length === 0 ? (
            <p className="px-2 py-1.5 text-ui text-fg-tertiary" data-testid="history-empty">
              {sessions.length === 0 ? copy.empty : fill(copy.noMatch, { query: query.trim() })}
            </p>
          ) : (
            sections.map((section) => (
              <div key={section.id} className="mb-4 flex flex-col gap-1" data-testid={`history-group-${section.id}`}>
                <h2 className="eyebrow px-2 uppercase tracking-wide">{section.label}</h2>
                <HistoryRailList
                  sessions={section.items}
                  currentId={currentId}
                  disabled={disabled}
                  onOpen={onOpen}
                  untitled={railCopy.untitled}
                  testId="see-all-row"
                />
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
