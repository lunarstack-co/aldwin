export const CONNECT_PATH_IDS = ['key', 'local', 'server'] as const;

export type ConnectPath = (typeof CONNECT_PATH_IDS)[number];

/** How long Connect waits for Aldwin to start: 40 checks, 250 ms apart. */
export const HOST_READY_POLL = { tries: 40, intervalMs: 250 } as const;
