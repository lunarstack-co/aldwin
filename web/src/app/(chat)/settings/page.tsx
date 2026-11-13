import type { Metadata } from "next";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.settings;

export default function SettingsPage() {
  return null;
}
