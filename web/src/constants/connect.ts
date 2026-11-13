export const CONNECT_PATH_IDS = ['key', 'local', 'server'] as const;

export type ConnectPath = (typeof CONNECT_PATH_IDS)[number];

/** About 10 s in all. */
export const HOST_READY_POLL = { tries: 40, intervalMs: 250 } as const;
