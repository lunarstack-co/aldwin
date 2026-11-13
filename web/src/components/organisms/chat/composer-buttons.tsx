"use client";

import { useAui } from "@assistant-ui/react";
import { AtSign, Blocks } from "lucide-react";
import { useMemo, useState, type RefObject } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import { settingsHref } from "@/constants/routes";
import appsCopy from "@/data/chat/apps-menu.json";
import composerCopy from "@/data/chat/composer.json";
import skillsCopy from "@/data/chat/skills-menu.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore } from "@/state/settings-store";
import { pickableApps, searchApps } from "@/utils/apps";
import { loadConnectors } from "@/utils/settings/actions";
import { searchSkills } from "@/utils/skills";
import { fill } from "@/utils/text";

import { addPickedApp } from "./apps-popover";
import { Button } from "@/components/ui/button";

import type { ComposerMenu as Menu, ComposerMenuProps } from "./composer-menu";

// The menu loads on first hover, focus or tap, so the chat page does not carry the popover.
const loadMenu = () => import("./composer-menu").then((m) => m.ComposerMenu);

function LazyComposerMenu(props: ComposerMenuProps) {
  const [loaded, setLoaded] = useState<{ Menu: typeof Menu } | null>(null);
  // The plain button stays until a click, so a keyboard user's focus is never on a button that gets swapped.
  const [clicked, setClicked] = useState(false);
  const prepare = () => {
    if (loaded === null) void loadMenu().then((Menu) => setLoaded({ Menu }));
  };
  if (loaded !== null && clicked) return <loaded.Menu {...props} initialOpen />;
  const { button } = props;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      disabled={button.disabled}
      aria-label={button.label}
      title={button.label}
      aria-haspopup="dialog"
      aria-expanded={false}
      data-testid={button.testId}
      className="max-md:size-10"
      onPointerEnter={prepare}
      onFocus={prepare}
      onClick={() => {
        setClicked(true);
        prepare();
      }}
    >
      {button.icon}
    </Button>
  );
}

const toolsLabel = (count: number) => (count === 1 ? appsCopy.oneTool : fill(appsCopy.tools, { count }));

/** Writes `@name ` at the caret, with a space before it when it would touch a word. */
function insertSkill(input: HTMLTextAreaElement, name: string, setText: (text: string) => void) {
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  const before = input.value.slice(0, start);
  const token = `${before === "" || /\s$/.test(before) ? "" : " "}@${name} `;
  setText(before + token + input.value.slice(end));
  const caret = start + token.length;
  requestAnimationFrame(() => {
    input.focus();
    input.setSelectionRange(caret, caret);
  });
}

export function SkillsButton({ inputRef, disabled }: { inputRef: RefObject<HTMLTextAreaElement | null>; disabled: boolean }) {
  const session = useSession();
  const aui = useAui();
  const skills = useChatStore((s) => s.skills);
  const online = useConnectionStore((s) => s.phase === "open");
  const status = skills.status === "ready" ? "ready" : skills.status === "error" ? "error" : "loading";

  return (
    <LazyComposerMenu
      testId="skills-button-menu"
      button={{ label: composerCopy.skills, icon: <AtSign aria-hidden />, testId: "composer-skills", disabled }}
      status={status}
      results={(query) =>
        searchSkills(skills.items, query).map((skill) => ({
          id: skill.name,
          label: skill.name,
          description: skill.description,
          icon: <AtSign aria-hidden />,
        }))
      }
      copy={{
        label: skillsCopy.label,
        search: skillsCopy.search,
        loading: skillsCopy.loading,
        none: skillsCopy.empty,
        noMatch: skillsCopy.noMatch,
        error: skillsCopy.error,
        retry: skillsCopy.retry,
        manage: skillsCopy.manage,
      }}
      manageHref={settingsHref("skills")}
      returnFocus={inputRef}
      onOpen={() => {
        if (online && skills.status === "idle") session.listSkills();
      }}
      onPick={(name) => {
        if (inputRef.current) insertSkill(inputRef.current, name, (text) => aui.composer().setText(text));
      }}
      onRetry={() => session.listSkills()}
    />
  );
}

export function AppsButton({ inputRef, disabled }: { inputRef: RefObject<HTMLTextAreaElement | null>; disabled: boolean }) {
  const session = useSession();
  const connectors = useSettingsStore((s) => s.connectors);
  const apps = useMemo(() => pickableApps(connectors.data.items), [connectors.data.items]);
  const status = connectors.status === "ready" ? "ready" : connectors.status === "error" ? "error" : "loading";

  return (
    <LazyComposerMenu
      testId="apps-button-menu"
      button={{ label: appsCopy.button, icon: <Blocks aria-hidden />, testId: "composer-apps-button", disabled }}
      status={status}
      results={(query) =>
        searchApps(apps, query).map((app) => ({
          id: app.name,
          label: app.title,
          description: toolsLabel(app.toolCount),
          icon: <BrandMark name={app.name} kind="connector" />,
        }))
      }
      copy={{
        label: appsCopy.label,
        search: appsCopy.search,
        loading: appsCopy.loading,
        none: appsCopy.none,
        noMatch: appsCopy.noMatch,
        error: appsCopy.error,
        retry: appsCopy.retry,
        manage: appsCopy.manage,
      }}
      manageHref={settingsHref("tools")}
      returnFocus={inputRef}
      onOpen={() => {
        if (connectors.status === "idle") void loadConnectors(session);
      }}
      onPick={(name) => {
        const app = apps.find((a) => a.name === name);
        if (app) addPickedApp(app);
      }}
      onRetry={() => void loadConnectors(session)}
    />
  );
}
