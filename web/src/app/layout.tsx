import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";

import { ThemeProvider } from "@/components/theme-provider";
import { THEME_COLORS } from "@/lib/theme";

import "./globals.css";

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
  title: "Aldwin",
  description: "Your personal assistant, running on this computer.",
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
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-control focus:bg-fg focus:px-3 focus:py-1.5 focus:text-ui focus:font-medium focus:text-fg-inverse"
          >
            Skip to content
          </a>
          <main id="main" className="flex flex-1 flex-col">
            {children}
          </main>
        </ThemeProvider>
      </body>
    </html>
  );
}
