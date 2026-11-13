import { PROFILE_PAGES, ROUTES } from "@/constants/routes";
import type { SetupStatus } from "@/types/setup";

function withSlash(path: string): string {
  return path.endsWith("/") ? path : `${path}/`;
}

/** `null` to stay. A model comes first because Aldwin cannot start without one; the profile never blocks. */
export function gateTarget(status: SetupStatus, pathname: string): string | null {
  const path = withSlash(pathname);
  if (!status.keys_ok) return path === ROUTES.connect ? null : ROUTES.connect;
  if (!status.profile_done) return PROFILE_PAGES.includes(path) ? null : ROUTES.name;
  // Setup is done, so any other page (a chat or not-found) stays put.
  return path === ROUTES.connect || PROFILE_PAGES.includes(path) ? ROUTES.home : null;
}
