import { shorten } from "@/utils/text";

export const TITLE_CHARS = 60;
export const RUNNING_MARK = "● ";

/** The tab title for a conversation: its name, a dot while Aldwin answers, then the app. */
export function pageTitle({
  title,
  firstUserText,
  running,
  fallback,
  app,
}: {
  title: string;
  firstUserText: string;
  running: boolean;
  fallback: string;
  app: string;
}): string {
  const name = shorten(title.trim() || firstUserText.trim() || fallback, TITLE_CHARS);
  return `${running ? RUNNING_MARK : ""}${name} · ${app}`;
}
