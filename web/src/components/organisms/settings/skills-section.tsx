"use client";

import { useEffect, useState } from "react";

import { MarkdownDocument } from "@/components/assistant-ui/elements/markdown-document";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import common from "@/data/settings/common.json";
import copy from "@/data/settings/skills.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore } from "@/state/settings-store";
import type { SkillCatalogItem } from "@/types/protocol";
import { showSkill } from "@/utils/settings/actions";
import { fill } from "@/utils/text";

const ERROR_FLAG = "error:";

// The YAML header is for Aldwin, not for reading.
const withoutFrontmatter = (body: string) => body.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "");

function SkillRow({ skill }: { skill: SkillCatalogItem }) {
  const session = useSession();
  const body = useSettingsStore((s) => (s.skillBody?.name === skill.name ? s.skillBody.body : null));
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const error = skill.flags.find((flag) => flag.startsWith(ERROR_FLAG))?.slice(ERROR_FLAG.length);
  const bodyId = `skill-body-${skill.name}`;

  async function toggle() {
    const next = !open;
    setOpen(next);
    setProblem(null);
    if (!next || body !== null) return;
    const outcome = await showSkill(session, skill.name);
    if (!outcome.ok) setProblem(outcome.message);
  }

  return (
    <li
      className="flex flex-col gap-2 rounded-lg border p-3"
      data-skill={skill.name}
      data-broken={error !== undefined || undefined}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-sm font-medium">@{skill.name}</span>
        <Badge variant="outline">{copy.source[skill.source]}</Badge>
        {error !== undefined ? <Badge variant="destructive">{copy.broken}</Badge> : null}
        <span className="text-muted-foreground text-xs">
          {skill.last_used
            ? fill(copy.lastUsed, {
                date: new Date(skill.last_used).toLocaleDateString(),
              })
            : copy.neverUsed}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void toggle()}
          aria-expanded={open}
          aria-controls={bodyId}
          className="ms-auto max-md:h-10"
        >
          {open ? copy.hide : copy.show}
        </Button>
      </div>
      <p className="text-muted-foreground text-sm">{skill.description}</p>
      {error !== undefined ? <p className="text-destructive text-sm">{error}</p> : null}
      {open ? (
        <div id={bodyId}>
          {problem !== null ? (
            <p role="alert" className="text-destructive text-sm">
              {problem}
            </p>
          ) : body === null ? (
            <p className="text-muted-foreground text-sm">{copy.loadingBody}</p>
          ) : (
            <div className="bg-muted/40 max-h-[28rem] overflow-auto rounded-md border px-4 py-3 text-sm">
              <MarkdownDocument text={withoutFrontmatter(body)} testId={`skill-body-${skill.name}`} />
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function SkillsSection() {
  const session = useSession();
  const skills = useChatStore((s) => s.skills);
  const open = useConnectionStore((s) => s.phase === "open");

  useEffect(() => {
    if (open && skills.status === "idle") session.listSkills();
  }, [open, skills.status, session]);

  if (skills.status === "error") {
    return (
      <Callout type="error" title={copy.loadError}>
        <Button variant="outline" size="sm" onClick={() => session.listSkills()}>
          {common.retry}
        </Button>
      </Callout>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-muted-foreground text-sm">{copy.lede}</p>
      {skills.status !== "ready" ? (
        <p className="text-muted-foreground text-sm">{common.loading}</p>
      ) : skills.items.length === 0 ? (
        <p className="text-muted-foreground text-sm">{copy.empty}</p>
      ) : (
        <ul className="flex flex-col gap-2" data-testid="skills-list">
          {skills.items.map((skill) => (
            <SkillRow key={skill.name} skill={skill} />
          ))}
        </ul>
      )}
    </div>
  );
}
