import type { Metadata } from "next";
import type { ReactNode } from "react";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.name;

export default function Layout({ children }: { children: ReactNode }) {
  return children;
}
