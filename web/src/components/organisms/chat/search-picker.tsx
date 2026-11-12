"use client";

import { ChevronDown, Globe, Settings } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { settingsHref } from "@/constants/routes";
import copy from "@/data/chat/search-picker.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore } from "@/state/settings-store";
import { loadSearch, makeSearchActive } from "@/utils/settings/actions";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

/** Which engine Aldwin searches the web with; only connected engines can be chosen here. */
export function SearchPicker({ disabled }: { disabled: boolean }) {
  const session = useSession();
  const router = useRouter();
  const search = useSettingsStore((s) => s.search);
  const ready = useConnectionStore((s) => s.phase === "open");
  const started = useChatStore((s) => s.sessionsLoaded);

  // The engine list waits for the conversation, so opening a chat stays as fast as before.
  useEffect(() => {
    if (ready && started && search.status === "idle") void loadSearch(session);
  }, [ready, started, search.status, session]);
  const connected = search.data.items.filter((item) => item.connected);
  const active = connected.find((item) => item.active) ?? null;
  const naming = ready && (search.status === "idle" || search.status === "loading");

  async function choose(name: string) {
    if (name === active?.name) return;
    const outcome = await makeSearchActive(session, name);
    if (!outcome.ok) useChatStore.getState().pushToast(outcome.message);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        disabled={disabled}
        render={
          <Button
            variant="ghost"
            size="sm"
            aria-label={active ? fill(copy.label, { name: active.title }) : copy.none}
            data-testid="search-picker"
            data-engine={active?.name ?? ""}
            className="max-w-[40%] gap-1.5 px-2 text-xs max-md:h-10 max-md:px-1.5 max-md:[&>svg:last-child]:hidden"
          />
        }
      >
        {active ? (
          <BrandMark name={active.name} kind="search" className="size-3.5" />
        ) : (
          <Globe aria-hidden className="size-3.5" />
        )}
        {/* Fades in once the list is here, so the empty label never flashes before the engine name. */}
        <span className={cn("truncate font-medium transition-opacity", naming && "opacity-0")}>
          {active?.title ?? copy.none}
        </span>
        <ChevronDown aria-hidden className="size-4 opacity-50" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side="top" className="min-w-52" data-testid="search-picker-menu">
        <DropdownMenuGroup>
          <DropdownMenuLabel>{copy.heading}</DropdownMenuLabel>
          {search.status !== "ready" ? (
            <p className="text-muted-foreground px-2 py-1.5 text-sm">{copy.loading}</p>
          ) : connected.length === 0 ? (
            <p className="text-muted-foreground px-2 py-1.5 text-sm">{copy.empty}</p>
          ) : (
            <DropdownMenuRadioGroup value={active?.name ?? ""} onValueChange={(value) => void choose(String(value))}>
              {connected.map((item) => (
                <DropdownMenuRadioItem key={item.name} value={item.name} closeOnClick data-engine={item.name}>
                  <BrandMark name={item.name} kind="search" className="size-3.5" />
                  {item.title}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          )}
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => router.push(settingsHref("search"))} data-testid="search-picker-manage">
          <Settings aria-hidden />
          {copy.manage}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
