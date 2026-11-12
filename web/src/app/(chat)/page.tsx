import type { Metadata } from "next";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.home;

/** A new chat; the shell in the layout draws it. */
export default function HomePage() {
  return null;
}
