import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.REVIEW_TEST_URL || "http://127.0.0.1:8765",
    channel: "chrome",
    viewport: { width: 1440, height: 960 },
    screenshot: "only-on-failure",
  },
});
