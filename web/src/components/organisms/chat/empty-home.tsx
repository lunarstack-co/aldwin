import copy from "@/data/chat/empty-home.json";

/** The question that opens an empty conversation. */
export function EmptyHome() {
  return (
    <div data-testid="empty-home" className="mb-6 flex flex-col items-center px-2">
      <h1 className="text-h1 font-semibold">{copy.hero}</h1>
    </div>
  );
}

/** The quiet promise under the composer on an empty conversation. */
export function TrustLine() {
  return <p className="text-center text-xs text-fg-tertiary">{copy.trust}</p>;
}
