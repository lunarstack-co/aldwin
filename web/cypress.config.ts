import { defineConfig } from "cypress";

export default defineConfig({
  e2e: {
    baseUrl: "http://127.0.0.1:4173",
    supportFile: "cypress/support/e2e.ts",
    video: false,
  },
});
