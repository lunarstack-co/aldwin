import { Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ANSWER_STYLE_IDS } from "@/constants/answer-styles";
import copy from "@/data/onboarding/personality.json";

/** Pick any number of answer styles; each stays pressed while chosen. */
export function AnswerStylePicker({
  chosen,
  onChange,
}: {
  chosen: string[];
  onChange: (chosen: string[]) => void;
}) {
  function toggle(id: string) {
    onChange(chosen.includes(id) ? chosen.filter((s) => s !== id) : [...chosen, id]);
  }

  return (
    <div role="group" aria-label={copy.groupLabel} className="flex flex-wrap gap-2">
      {ANSWER_STYLE_IDS.map((id) => {
        const pressed = chosen.includes(id);
        return (
          <Button
            key={id}
            variant={pressed ? "default" : "outline"}
            aria-pressed={pressed}
            onClick={() => toggle(id)}
            className="max-md:h-10"
          >
            {pressed ? <Check aria-hidden /> : null}
            {copy.styles[id]}
          </Button>
        );
      })}
    </div>
  );
}
