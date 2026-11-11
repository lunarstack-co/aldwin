import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { CONNECT_PATH_IDS, type ConnectPath } from "@/constants/connect";
import copy from "@/data/onboarding/connect.json";

/** The three ways to give Aldwin a model, as cards with one chosen. */
export function ConnectPathPicker({
  value,
  onChange,
}: {
  value: ConnectPath;
  onChange: (path: ConnectPath) => void;
}) {
  return (
    <RadioGroup
      aria-label={copy.pathsLabel}
      value={value}
      onValueChange={(next) => onChange(next as ConnectPath)}
      className="grid gap-2 sm:grid-cols-3"
    >
      {CONNECT_PATH_IDS.map((id) => (
        <Label
          key={id}
          htmlFor={`path-${id}`}
          className="has-data-checked:border-foreground/40 has-data-checked:bg-foreground/[0.04] flex min-h-11 cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 text-sm font-normal"
        >
          <RadioGroupItem id={`path-${id}`} value={id} />
          {copy.paths[id]}
        </Label>
      ))}
    </RadioGroup>
  );
}
