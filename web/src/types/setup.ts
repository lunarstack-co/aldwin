/** Shapes of the first-run routes of `aldwin web`. */

export type ProfileAnswers = {
  first_name: string;
  occupation: string;
  personality: string[];
};

export type ProfileUpdate = Partial<ProfileAnswers> & {
  complete?: boolean;
  skipped?: boolean;
};

export type SetupStatus = {
  keys_ok: boolean;
  host_ready: boolean;
  profile_done: boolean;
  profile_skipped: boolean;
  profile: ProfileAnswers;
  model: string;
  provider: string;
  is_local: boolean;
};

export type ModelOption = { id: string; title: string; local: boolean };

export type LocalRuntime = {
  name: string;
  title: string;
  running: boolean;
  models: ModelOption[];
};

export type ProviderOption = { name: string; title: string };

/** A model the user can pick, as `provider/model`. */
export type ModelChoice = { ref: string; label: string };
