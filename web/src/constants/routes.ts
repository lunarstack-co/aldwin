/** Trailing slashes match what the static export serves. */
export const ROUTES = {
  home: "/",
  conversation: "/c/",
  settings: "/settings/",
  connect: "/onboarding/connect/",
  name: "/onboarding/name/",
  occupation: "/onboarding/occupation/",
  personality: "/onboarding/personality/",
} as const;

export const REPORT_BUG_URL = "https://github.com/lunarstack-co/aldwin/issues/new?template=bug_report.yml";

export const PROFILE_PAGES: readonly string[] = [
  ROUTES.name,
  ROUTES.occupation,
  ROUTES.personality,
];

export function isHomePage(pathname: string): boolean {
  return pathname === ROUTES.home || pathname === "";
}

export function isSettingsPage(pathname: string): boolean {
  return pathname === ROUTES.settings || `${pathname}/` === ROUTES.settings;
}

export function settingsHref(section?: string): string {
  return section ? `${ROUTES.settings}?section=${encodeURIComponent(section)}` : ROUTES.settings;
}

export function isConversationPage(pathname: string): boolean {
  return pathname === ROUTES.conversation || `${pathname}/` === ROUTES.conversation;
}

/** The open conversation once it has turns, otherwise a new chat. */
export function chatHref(currentId: string | null, hasTurns: boolean): string {
  return currentId !== null && hasTurns ? conversationHref(currentId) : ROUTES.home;
}

/** The static export serves `/c/` for every id, so the id goes in the query. */
export function conversationHref(id: string): string {
  return `${ROUTES.conversation}?id=${encodeURIComponent(id)}`;
}
