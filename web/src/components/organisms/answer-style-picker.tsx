import { ToggleChip } from "@/components/molecules/toggle-chip";
import { ANSWER_STYLE_IDS } from "@/constants/answer-styles";
import copy from "@/data/onboarding/personality.json";

/** Pick any number of answer styles. */
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
      {ANSWER_STYLE_IDS.map((id) => (
        <ToggleChip key={id} pressed={chosen.includes(id)} onToggle={() => toggle(id)}>
          {copy.styles[id]}
        </ToggleChip>
      ))}
    </div>
  );
}
