"use client";

import { FileCode2, SlidersHorizontal } from "lucide-react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useState, useSyncExternalStore } from "react";

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
import { THEMES } from "@/constants/theme";
import densityCopy from "@/data/chat/density-toggle.json";
import copy from "@/data/chat/header.json";
import themeCopy from "@/data/theme-toggle.json";
import { useDensity } from "@/hooks/use-density";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { isDensity } from "@/utils/density";

const ConversationJsonSheetLazy = dynamic(
  () =>
    import("./conversation-json-sheet").then((m) => m.ConversationJsonSheet),
  { ssr: false },
);

const noop = () => () => {};

export function ViewMenu() {
  const [density, setDensity] = useDensity();
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(noop, () => true, () => false);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const phase = useConnectionStore((s) => s.phase);
  const [jsonOpen, setJsonOpen] = useState(false);
  const canOpenJson = Boolean(currentSessionId) && phase === "open";

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon"
              aria-label={copy.menu}
              title={copy.menu}
              data-testid="density-toggle"
              className="max-md:size-10"
            />
          }
        >
          <SlidersHorizontal aria-hidden />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-44">
          <DropdownMenuGroup>
            <DropdownMenuLabel>{densityCopy.label}</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={density}
              onValueChange={(value) => {
                if (isDensity(value)) setDensity(value);
              }}
            >
              <DropdownMenuRadioItem value="clean" data-testid="density-clean">
                {densityCopy.clean}
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="detail" data-testid="density-detail">
                {densityCopy.detail}
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>{themeCopy.label}</DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={mounted ? theme : undefined}
              onValueChange={(value) => setTheme(String(value))}
            >
              {THEMES.map((id) => (
                <DropdownMenuRadioItem key={id} value={id} data-testid={`theme-${id}`}>
                  {themeCopy[id]}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem
              data-testid="conversation-json-trigger"
              disabled={!canOpenJson}
              onClick={() => {
                if (canOpenJson) setJsonOpen(true);
              }}
            >
              <FileCode2 aria-hidden />
              {copy.conversationJson}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      {jsonOpen ? (
        <ConversationJsonSheetLazy
          open={jsonOpen}
          onOpenChange={setJsonOpen}
          sessionId={currentSessionId}
        />
      ) : null}
    </>
  );
}
