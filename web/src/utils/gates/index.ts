import { PROFILE_PAGES, ROUTES } from "@/constants/routes";
import type { SetupStatus } from "@/types/setup";

function withSlash(path: string): string {
  return path.endsWith("/") ? path : `${path}/`;
}

/**
 * Where the page should be for this status, or `null` to stay. A model comes
 * first, because Aldwin cannot start without one; the profile can always be
 * skipped and never holds anything up.
 */
export function gateTarget(status: SetupStatus, pathname: string): string | null {
  const path = withSlash(pathname);
  if (!status.keys_ok) return path === ROUTES.connect ? null : ROUTES.connect;
  if (!status.profile_done) return PROFILE_PAGES.includes(path) ? null : ROUTES.name;
  // Setup is done: onboarding is behind us, and anything else (a chat or the
  // not-found page) is shown where it is.
  return path === ROUTES.connect || PROFILE_PAGES.includes(path) ? ROUTES.home : null;
}
