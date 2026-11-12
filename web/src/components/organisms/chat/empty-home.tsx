import { Mark } from "@/components/brand/mark";
import copy from "@/data/chat/empty-home.json";

/** The question that opens an empty conversation, above the centred composer. */
export function EmptyHome() {
  return (
    <div data-testid="empty-home" className="mb-6 flex flex-col items-center gap-4 px-2 text-center">
      <Mark animated className="size-10" />
      <h1 className="text-2xl">{copy.hero}</h1>
    </div>
  );
}
