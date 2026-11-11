/** Every page, with the trailing slash the static export serves. */
export const ROUTES = {
  home: "/",
  conversation: "/c/",
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

/** True on `/c/`, with or without the trailing slash. */
export function isConversationPage(pathname: string): boolean {
  return pathname === ROUTES.conversation || `${pathname}/` === ROUTES.conversation;
}

/** One conversation's address; the static export serves `/c/` for every id. */
export function conversationHref(id: string): string {
  return `${ROUTES.conversation}?id=${encodeURIComponent(id)}`;
}
