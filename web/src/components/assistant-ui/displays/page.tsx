"use client";

import { LinkPreview } from "@/components/assistant-ui/elements/link-preview";
import type { PageDisplay } from "@/types/protocol";

export function PageDisplayView({ display }: { display: PageDisplay }) {
  return (
    <LinkPreview
      data-testid="display-page"
      href={display.url}
      title={display.title || undefined}
      siteName={display.site || undefined}
      description={display.description || undefined}
      layout="compact"
      className="max-w-full"
    />
  );
}
