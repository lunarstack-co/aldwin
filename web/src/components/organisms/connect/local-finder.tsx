import { Button } from "@/components/ui/button";
import copy from "@/data/onboarding/local-finder.json";

export function LocalFinder({ busy, onFind }: { busy: boolean; onFind: () => void }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-muted-foreground text-base">{copy.lede}</p>
      <Button disabled={busy} onClick={onFind} className="self-start max-md:h-10">
        {copy.submit}
      </Button>
    </div>
  );
}
