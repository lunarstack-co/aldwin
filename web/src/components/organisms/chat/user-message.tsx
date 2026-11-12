"use client";

import { MessagePrimitive } from "@assistant-ui/react";
import { AtSign, Check } from "lucide-react";
import dynamic from "next/dynamic";

import { createDirectiveText } from "@/components/assistant-ui/elements/directive-text";
import { useMessageTurn } from "@/components/assistant-ui/turn";
import { Badge } from "@/components/ui/badge";
import copy from "@/data/chat/transcript.json";
import appsCopy from "@/data/chat/apps-menu.json";
import skillsCopy from "@/data/chat/skills-menu.json";
import { useChatStore } from "@/state/chat-store";
import { skillFormatter } from "@/utils/skills";
import { fill } from "@/utils/text";
import { dayKey, formatClockTime, formatDayLabel } from "@/utils/time";

import { PickedAppList } from "./picked-apps";

const DaySeparatorLineLazy = dynamic(
  () =>
    import("@/components/assistant-ui/elements/day-separator").then(
      (m) => m.DaySeparatorLine,
    ),
  { ssr: false },
);

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

function useTurnDaySeparator(turnId: string | undefined, userAt: string | null | undefined): string | null {
  const turns = useChatStore((s) => s.turns);
  if (!turnId || !userAt) return null;
  const currentKey = dayKey(userAt);
  if (!currentKey) return null;

  const idx = turns.findIndex((t) => t.id === turnId);
  let prevKey: string | null = null;
  for (let i = idx - 1; i >= 0; i -= 1) {
    const candidate = dayKey(turns[i]?.userAt ?? turns[i]?.assistantAt);
    if (candidate) {
      prevKey = candidate;
      break;
    }
  }
  if (prevKey === currentKey) return null;
  return formatDayLabel(userAt, {
    todayLabel: copy.today,
    yesterdayLabel: copy.yesterday,
  });
}

/** What the user asked, kept to the right so the answer column reads clean. */
export function UserMessage() {
  const turn = useMessageTurn();
  const separatorLabel = useTurnDaySeparator(turn?.id, turn?.userAt);
  const clockTime = formatClockTime(turn?.userAt);

  return (
    <>
      {separatorLabel ? <DaySeparatorLineLazy label={separatorLabel} /> : null}
      <MessagePrimitive.Root
        className="group/user flex flex-col items-end gap-1.5"
        data-testid="user-message"
      >
        <div className="flex w-full items-baseline justify-end gap-2">
          {clockTime && turn?.userAt ? (
            <time
              dateTime={turn.userAt}
              tabIndex={0}
              data-testid="message-time"
              className="text-muted-foreground shrink-0 font-mono text-xs tabular-nums opacity-0 transition-opacity outline-none group-hover/user:opacity-100 group-focus-within/user:opacity-100 focus:opacity-100"
            >
              {clockTime}
            </time>
          ) : null}
          <div
            aria-label={copy.you}
            data-testid="user-bubble"
            className="bg-muted text-foreground max-w-[80%] rounded-thread px-4 py-2.5 text-base whitespace-pre-wrap break-words"
          >
            <MessagePrimitive.Parts components={{ Text: SkillText }} />
          </div>
        </div>
        <AppsPicked />
        <SkillsUsed />
      </MessagePrimitive.Root>
    </>
  );
}
