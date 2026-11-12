"use client";

import { WebSearch } from "@/components/assistant-ui/elements/web-search";
import { hostOf } from "@/components/assistant-ui/utils/href";
import copy from "@/data/chat/displays.json";
import type { SearchDisplay } from "@/types/protocol";
import { fill } from "@/utils/text";

export function SearchDisplayView({ display }: { display: SearchDisplay }) {
  const results = display.results.map((hit) => ({
    title: hit.title || hit.url,
    domain: hostOf(hit.url) ?? hit.url,
    url: hit.url,
    snippet: hit.snippet,
  }));
  const count = results.length;
  const statusText =
    count === 0
      ? copy.search.none
      : count === 1
        ? copy.search.one
        : fill(copy.search.many, { count });

  return (
    <WebSearch
      data-testid="display-search"
      query={display.query}
      results={results}
      visibleResults={count}
      searching={false}
      cycle={1}
      statusText={statusText}
      footer={display.truncated ? fill(copy.truncated, { count }) : undefined}
      className="max-w-full"
    />
  );
}
