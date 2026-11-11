import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";

import { THEME_COLORS } from "@/constants/theme";
import copy from "@/data/app.json";
import { AppShell } from "@/layouts/app-shell";
import { SetupProvider } from "@/providers/setup-provider";
import { ThemeProvider } from "@/providers/theme-provider";

import "@/styles/globals.css";

// The official Mona Sans release (OFL, licence beside the files), bundled so
// the local UI never fetches fonts from the network.
const monaSans = localFont({
  src: "../assets/fonts/mona-sans/MonaSansVF.woff2",
  variable: "--font-mona",
  weight: "200 900",
  display: "swap",
});

const monaSansMono = localFont({
  src: "../assets/fonts/mona-sans/MonaSansMonoVF.woff2",
  variable: "--font-mona-mono",
  weight: "200 900",
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: copy.title,
  description: copy.description,
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: THEME_COLORS.dark },
    { media: "(prefers-color-scheme: light)", color: THEME_COLORS.light },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${monaSans.variable} ${monaSansMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-base font-sans text-fg">
        <ThemeProvider>
          <AppShell>
            <SetupProvider>{children}</SetupProvider>
          </AppShell>
        </ThemeProvider>
      </body>
    </html>
  );
}
