"use client";

import { Eye, EyeOff } from "lucide-react";
import { useState, type ComponentProps } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import copy from "@/data/onboarding/secret.json";

export function SecretInput(props: Omit<ComponentProps<typeof Input>, "type">) {
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <Input
        {...props}
        type={shown ? "text" : "password"}
        autoComplete="off"
        spellCheck={false}
        className="pe-11 max-md:h-10"
      />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-pressed={shown}
        aria-label={shown ? copy.hide : copy.show}
        title={shown ? copy.hide : copy.show}
        onClick={() => setShown(!shown)}
        className="absolute top-1/2 right-1 -translate-y-1/2 max-md:size-9"
      >
        {shown ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
      </Button>
    </div>
  );
}
