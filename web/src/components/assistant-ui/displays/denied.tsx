"use client";

import { GuardrailNotice } from "@/components/assistant-ui/elements/guardrail-notice";
import copy from "@/data/chat/displays.json";
import type { DeniedDisplay } from "@/types/protocol";

export function DeniedDisplayView({ display }: { display: DeniedDisplay }) {
  const isUser = display.rule === "user" || display.rule === "user_edit";
  return (
    <GuardrailNotice
      data-testid="display-denied"
      title={isUser ? copy.denied.userTitle : copy.denied.title}
      policy={display.rule}
      explanation={display.message}
      alternatives={[]}
      className="max-w-full"
    />
  );
}
