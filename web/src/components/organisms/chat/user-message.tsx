"use client";

import { MessagePrimitive } from "@assistant-ui/react";
import { AtSign, Check } from "lucide-react";

import { createDirectiveText } from "@/components/assistant-ui/elements/directive-text";
import { useMessageTurn } from "@/components/assistant-ui/turn";
import { Badge } from "@/components/ui/badge";
import copy from "@/data/chat/transcript.json";
import appsCopy from "@/data/chat/apps-menu.json";
import skillsCopy from "@/data/chat/skills-menu.json";
import { skillFormatter } from "@/utils/skills";
import { fill } from "@/utils/text";

import { PickedAppList } from "./picked-apps";

// `@name` in a sent message shows as a chip, read the same way the composer wrote it.
export const SkillText = createDirectiveText(skillFormatter, { fallbackIcon: AtSign });

/** The skills the host used for this turn, and a line for each one it left out. */
function SkillsUsed() {
  const used = useMessageTurn()?.skillsUsed;
  if (!used) return null;
  return (
    <div className="flex max-w-[80%] flex-col items-end gap-1 text-xs" data-testid="skills-used">
      {used.names.length > 0 ? (
        <ul aria-label={skillsCopy.used} className="flex flex-wrap justify-end gap-1">
          {used.names.map((name) => (
            <li key={name}>
              <Badge variant="outline" data-skill-used={name}>
                <Check aria-hidden />
                {name}
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
      {used.dropped.map(({ name, reason }) => (
        <p key={name} className="text-muted-foreground" data-skill-dropped={reason}>
          {fill(reason === "limit" ? skillsCopy.droppedLimit : skillsCopy.droppedUnknown, { name })}
        </p>
      ))}
    </div>
  );
}

/** The apps picked for this turn as chips, and a line for each the host could not use. */
function AppsPicked() {
  const turn = useMessageTurn();
  if (!turn || turn.pickedApps.length === 0) return null;
  const title = (name: string) => turn.pickedApps.find((a) => a.name === name)?.title ?? name;
  return (
    <div className="flex max-w-[80%] flex-col items-end gap-1 text-xs" data-testid="apps-picked">
      <PickedAppList apps={turn.pickedApps} className="justify-end" />
      {turn.appsUsed?.dropped.map(({ name, reason }) => (
        <p key={name} className="text-muted-foreground" data-app-dropped={reason}>
          {fill(reason === "limit" ? appsCopy.droppedLimit : appsCopy.droppedUnknown, { app: title(name) })}
        </p>
      ))}
    </div>
  );
}

/** What the user asked, kept to the right so the answer column reads clean. */
export function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex flex-col items-end gap-1.5" data-testid="user-message">
      <div
        aria-label={copy.you}
        className="bg-muted text-foreground max-w-[80%] rounded-thread px-4 py-2.5 text-base whitespace-pre-wrap break-words"
      >
        <MessagePrimitive.Parts components={{ Text: SkillText }} />
      </div>
      <AppsPicked />
      <SkillsUsed />
    </MessagePrimitive.Root>
  );
}
