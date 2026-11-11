import { Button } from "@/components/atoms/button";
import { CONNECT_PATH_IDS, type ConnectPath } from "@/constants/connect";
import copy from "@/data/onboarding/connect.json";

export function ConnectPathPicker({
  value,
  onChange,
}: {
  value: ConnectPath;
  onChange: (path: ConnectPath) => void;
}) {
  return (
    <div role="group" aria-label={copy.pathsLabel} className="flex flex-wrap gap-2">
      {CONNECT_PATH_IDS.map((id) => (
        <Button
          key={id}
          variant={value === id ? "primary" : "outline"}
          aria-pressed={value === id}
          onClick={() => onChange(id)}
        >
          {copy.paths[id]}
        </Button>
      ))}
    </div>
  );
}
