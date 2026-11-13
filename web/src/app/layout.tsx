import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";

import { THEME_COLORS } from "@/constants/theme";
import copy from "@/data/app.json";
import { AppShell } from "@/layouts/app-shell";
import { SetupProvider } from "@/providers/setup-provider";
import { ThemeProvider } from "@/providers/theme-provider";

import "@/styles/globals.css";

// Fonts are bundled so the local UI never fetches them.
const publicSans = localFont({
  src: "../assets/fonts/public-sans/PublicSansVF-latin.woff2",
  variable: "--font-public-sans",
  weight: "100 900",
  display: "swap",
});

const jetBrainsMono = localFont({
  src: "../assets/fonts/jetbrains-mono/JetBrainsMonoVF-latin.woff2",
  variable: "--font-jetbrains-mono",
  weight: "100 800",
  display: "swap",
});

// Local-only, so never indexed.
export const metadata: Metadata = {
  title: { default: copy.title, template: `%s · ${copy.title}` },
  description: copy.description,
  applicationName: copy.title,
  robots: { index: false, follow: false },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: THEME_COLORS.light },
    { media: "(prefers-color-scheme: dark)", color: THEME_COLORS.dark },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${publicSans.variable} ${jetBrainsMono.variable} h-full`}
    >
      <body className="flex h-full flex-col">
        <ThemeProvider>
          <AppShell>
            <SetupProvider>{children}</SetupProvider>
          </AppShell>
        </ThemeProvider>
      </body>
    </html>
  );
}
