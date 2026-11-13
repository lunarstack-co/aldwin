export const DENSITY_KEY = 'aldwin.density';
export const DENSITIES = ['clean', 'detail'] as const;

export const WS_PATH = '/v1/ws';
export const WS_TICKET_PATH = '/v1/ws-ticket';
export const HISTORY_LIMIT = 500;
export const RAIL_HISTORY_LIMIT = 15;

/** Any other close code counts as a dropped connection. */
export const CLOSE_CODES = {
  goingAway: 1001,
  replaced: 4000,
  alreadyAttached: 4409,
} as const;

export const RECONNECT = {
  attempts: 5,
  baseDelayMs: 500,
  maxDelayMs: 8000,
} as const;

export const COPY_FEEDBACK_MS = 1800;
export const WORK_TICK_MS = 1000;
/** Shorter waits show nothing, so a fast reload never flashes a placeholder. */
export const LOADING_DELAY_MS = 400;
