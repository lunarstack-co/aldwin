import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// The dev server proxies the API to a local `aldwin web`. In production one
// Python process serves the static export and the API from the same origin.
const DEV_API = "http://127.0.0.1:7841";

export default function config(phase: string): NextConfig {
  const shared: NextConfig = {
    images: { unoptimized: true },
    turbopack: { root: __dirname },
    // Keep `next dev` from writing an AGENTS.md into the repo.
    agentRules: false,
    // Each page exports as <route>/index.html, which the Python static files
    // server finds without a .html extension.
    trailingSlash: true,
  };
  if (phase !== PHASE_DEVELOPMENT_SERVER) {
    return { ...shared, output: "export" };
  }
  // Rewrites cannot coexist with a static export, so the dev server leaves
  // `output` unset. `npm run build` still enforces every export restriction.
  return {
    ...shared,
    async rewrites() {
      return [{ source: "/v1/:path*", destination: `${DEV_API}/v1/:path*` }];
    },
  };
}
