import { Button } from "@/components/atoms/button";
import copy from "@/data/onboarding/local-finder.json";

export function LocalFinder({ busy, onFind }: { busy: boolean; onFind: () => void }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-md text-fg-secondary">{copy.lede}</p>
      <Button variant="primary" disabled={busy} onClick={onFind} className="self-start">
        {copy.submit}
      </Button>
    </div>
  );
}
