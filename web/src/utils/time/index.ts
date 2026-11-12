export function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A duration for people: "840 ms", "12.4 s", "1 min 15 s". */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  if (ms < 60_000) return `${(Math.floor(ms / 100) / 10).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} min ${Math.floor((ms % 60_000) / 1000)} s`;
}

function sameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function dayKey(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function formatDayLabel(
  iso: string | null | undefined,
  {
    now = new Date(),
    locale,
    todayLabel = "Today",
    yesterdayLabel = "Yesterday",
  }: {
    now?: Date;
    locale?: string;
    todayLabel?: string;
    yesterdayLabel?: string;
  } = {},
): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  if (sameLocalDay(date, now)) return todayLabel;
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameLocalDay(date, yesterday)) return yesterdayLabel;
  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);
}

export function formatClockTime(iso: string | null | undefined, locale?: string): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export type TimingMetrics = {
  firstTokenSeconds: string;
  tokensPerSecond: string;
  rawFirstTokenMs: number;
  rawTokensPerSecond: number;
};

export function computeMessageTiming(
  startedAt: number,
  firstTokenAt: number | null,
  endedAt: number | null,
  outputTokens: number,
): TimingMetrics | null {
  if (
    startedAt <= 0 ||
    firstTokenAt === null ||
    endedAt === null ||
    outputTokens <= 0 ||
    endedAt <= startedAt
  ) {
    return null;
  }
  const ttftMs = Math.max(0, firstTokenAt - startedAt);
  const streamWindowMs = endedAt > firstTokenAt ? endedAt - firstTokenAt : endedAt - startedAt;
  if (streamWindowMs <= 0) return null;
  const tps = outputTokens / (streamWindowMs / 1000);
  return {
    firstTokenSeconds: `${(ttftMs / 1000).toFixed(1)} s`,
    tokensPerSecond: `${Math.round(tps)} tok/s`,
    rawFirstTokenMs: ttftMs,
    rawTokensPerSecond: tps,
  };
}
