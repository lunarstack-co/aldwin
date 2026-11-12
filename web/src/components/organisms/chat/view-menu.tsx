"use client";

import { SlidersHorizontal } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
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
import { isDensity } from "@/utils/density";

const noop = () => () => {};

/**
 * How the page looks in this browser: transcript density and theme. Neither
 * reaches the server.
 */
export function ViewMenu() {
  const [density, setDensity] = useDensity();
  const { theme, setTheme } = useTheme();
  // The prerendered page knows no theme; only the browser does.
  const mounted = useSyncExternalStore(noop, () => true, () => false);
  return (
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
          <DropdownMenuRadioGroup value={mounted ? theme : undefined} onValueChange={(value) => setTheme(String(value))}>
            {THEMES.map((id) => (
              <DropdownMenuRadioItem key={id} value={id} data-testid={`theme-${id}`}>
                {themeCopy[id]}
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
