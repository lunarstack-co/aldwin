import type { Metadata } from "next";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.home;

export default function HomePage() {
  return null;
}
