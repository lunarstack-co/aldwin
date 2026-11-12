import type { Metadata } from "next";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.settings;

/** The Settings hub; the shell in the layout draws it in place of the conversation. */
export default function SettingsPage() {
  return null;
}
