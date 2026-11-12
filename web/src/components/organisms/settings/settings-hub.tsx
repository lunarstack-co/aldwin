"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import type { ComponentType } from "react";

import { buttonVariants } from "@/components/ui/button";
import { chatHref, settingsHref } from "@/constants/routes";
import copy from "@/data/settings/hub.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { SETTINGS_SECTIONS, sectionOf, type SettingsSection } from "@/utils/settings";

import { AdvancedSection } from "./advanced-section";
import { AppearanceSection } from "./appearance-section";
import { ProvidersSection } from "./providers-section";
import { SearchSection } from "./search-section";
import { SkillsSection } from "./skills-section";
import { ToolsSection } from "./tools-section";

const SECTIONS: Record<SettingsSection, ComponentType> = {
  providers: ProvidersSection,
  search: SearchSection,
  skills: SkillsSection,
  tools: ToolsSection,
  advanced: AdvancedSection,
  appearance: AppearanceSection,
};

/** The six Settings sections; the address names the open one, so Back and Forward move between them. */
export function SettingsHub() {
  const section = sectionOf(useSearchParams().get("section"));
  const currentId = useChatStore((s) => s.currentSessionId);
  const hasTurns = useChatStore((s) => s.turns.length > 0);
  const back = chatHref(currentId, hasTurns);
  const Section = SECTIONS[section];
  const text = copy.sections[section];

  return (
    <div data-testid="settings-hub" className="flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-6 px-4 py-6 sm:px-6 md:flex-row md:gap-10">
        <div className="flex shrink-0 flex-col gap-3 md:sticky md:top-6 md:w-48 md:self-start">
          <Link href={back} className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "self-start max-md:h-10")}>
            <ArrowLeft aria-hidden />
            {copy.back}
          </Link>
          <nav aria-label={copy.label} data-testid="settings-nav">
            <ul className="-mx-1 flex gap-1 overflow-x-auto px-1 md:flex-col md:overflow-visible">
              {SETTINGS_SECTIONS.map((id) => (
                <li key={id} className="shrink-0">
                  <Link
                    href={settingsHref(id)}
                    aria-current={id === section ? "page" : undefined}
                    data-section={id}
                    className={cn(
                      "text-muted-foreground hover:bg-accent hover:text-accent-foreground flex rounded-md px-3 py-1.5 text-sm whitespace-nowrap max-md:min-h-10 max-md:items-center",
                      id === section && "bg-accent text-accent-foreground font-medium",
                    )}
                  >
                    {copy.sections[id].label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </div>
        <section
          aria-labelledby="settings-section-title"
          className="flex min-w-0 flex-1 flex-col gap-5"
          data-testid={`settings-${section}`}
        >
          <header className="flex flex-col gap-1">
            <h2 id="settings-section-title" className="text-xl">
              {text.label}
            </h2>
            <p className="text-muted-foreground text-sm">{text.lede}</p>
          </header>
          <Section />
        </section>
      </div>
    </div>
  );
}
