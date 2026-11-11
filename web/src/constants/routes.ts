/** Every page, with the trailing slash the static export serves. */
export const ROUTES = {
  home: "/",
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
