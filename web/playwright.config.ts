import { defineConfig, devices, type Project } from "@playwright/test";

const CI = Boolean(process.env.CI);
const BASE_URL = "http://127.0.0.1:4173";
// The kit gallery exists only under `next dev`, and Next allows one dev server per
// directory, so its projects run on request: PW_KIT=1, and PW_KIT_URL to reuse a running one.
const KIT = process.env.PW_KIT === "1";
const KIT_URL = process.env.PW_KIT_URL ?? "http://127.0.0.1:3100";

// Functional specs run on desktop Chromium in both themes; specs tagged
// @cross also run on Firefox, WebKit and the two phones.
const browsers = [
  { name: "chromium", device: devices["Desktop Chrome"], cross: false },
  { name: "firefox", device: devices["Desktop Firefox"], cross: true },
  { name: "webkit", device: devices["Desktop Safari"], cross: true },
  { name: "mobile-chrome", device: devices["Pixel 7"], cross: true },
  { name: "mobile-safari", device: devices["iPhone 15"], cross: true },
] as const;

const projects: Project[] = browsers.flatMap(({ name, device, cross }) =>
  (["light", "dark"] as const).map((colorScheme) => ({
    name: `${name}-${colorScheme}`,
    testIgnore: /kit\.spec\.ts/,
    grep: cross ? /@cross/ : undefined,
    use: {
      ...device,
      ...(name === "chromium" || name === "firefox" || name === "webkit" ? { viewport: { width: 1280, height: 800 } } : {}),
      colorScheme,
      baseURL: BASE_URL,
    },
  })),
);

if (KIT) projects.push(
  ...(["light", "dark"] as const).map((colorScheme) => ({
    name: `kit-${colorScheme}`,
    testMatch: /kit\.spec\.ts/,
    use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, colorScheme, baseURL: KIT_URL },
  })),
);

export default defineConfig({
  testDir: "playwright",
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 2 : 0,
  workers: CI ? 4 : undefined,
  reporter: CI ? [["html", { open: "never" }], ["github"]] : [["list"]],
  use: {
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects,
  webServer: [
    {
      command: "node playwright/serve.mjs out 4173",
      url: BASE_URL,
      reuseExistingServer: !CI,
    },
    ...(KIT
      ? [
          {
            command: "npx next dev -p 3100",
            url: `${KIT_URL}/dev/kit/`,
            reuseExistingServer: true,
            timeout: 180_000,
          },
        ]
      : []),
  ],
});
