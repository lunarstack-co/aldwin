/** Every page, with the trailing slash the static export serves. */
export const ROUTES = {
  home: "/",
  conversation: "/c/",
  settings: "/settings/",
  connect: "/onboarding/connect/",
  name: "/onboarding/name/",
  occupation: "/onboarding/occupation/",
  personality: "/onboarding/personality/",
} as const;

export const PROFILE_PAGES: readonly string[] = [
  ROUTES.name,
  ROUTES.occupation,
  ROUTES.personality,
];

/** True on `/`, the new-chat page. */
export function isHomePage(pathname: string): boolean {
  return pathname === ROUTES.home || pathname === "";
}

/** True on `/settings/`, with or without the trailing slash. */
export function isSettingsPage(pathname: string): boolean {
  return pathname === ROUTES.settings || `${pathname}/` === ROUTES.settings;
}

/** A Settings address, opened on one section when given. */
export function settingsHref(section?: string): string {
  return section ? `${ROUTES.settings}?section=${encodeURIComponent(section)}` : ROUTES.settings;
}

/** True on `/c/`, with or without the trailing slash. */
export function isConversationPage(pathname: string): boolean {
  return pathname === ROUTES.conversation || `${pathname}/` === ROUTES.conversation;
}

/** The chat to go back to: the open conversation once it has turns, otherwise a new chat. */
export function chatHref(currentId: string | null, hasTurns: boolean): string {
  return currentId !== null && hasTurns ? conversationHref(currentId) : ROUTES.home;
}

/** One conversation's address; the static export serves `/c/` for every id. */
export function conversationHref(id: string): string {
  return `${ROUTES.conversation}?id=${encodeURIComponent(id)}`;
}
