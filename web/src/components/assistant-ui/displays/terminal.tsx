"use client";

import { TerminalBlock } from "@/components/assistant-ui/elements/terminal-block";
import copy from "@/data/chat/displays.json";
import type { TerminalDisplay } from "@/types/protocol";
import { fill } from "@/utils/text";

function splitStream(text: string): string[] {
  if (!text) return [];
  const trimmed = text.endsWith("\n") ? text.slice(0, -1) : text;
  return trimmed ? trimmed.split("\n") : [];
}

export function TerminalDisplayView({
  display,
  pending = false,
}: {
  display: TerminalDisplay;
  pending?: boolean;
}) {
  const stdoutLines = splitStream(display.stdout);
  const stderrLines = splitStream(display.stderr);
  const totalLines = stdoutLines.length + stderrLines.length;
  const done = !pending && display.exit_code !== null;

  return (
    <TerminalBlock
      data-testid="display-terminal"
      command={display.command}
      cwd={display.cwd || undefined}
      lines={stdoutLines}
      stderr={stderrLines}
      visibleCount={totalLines}
      done={done}
      pending={pending}
      exitCode={display.exit_code ?? undefined}
      durationMs={done ? display.duration_ms : undefined}
      maxCollapsedLines={20}
      truncated={display.truncated}
      truncatedLabel={fill(copy.truncated, { count: Math.max(totalLines, 400) })}
      className="max-w-full"
    />
  );
}
