// Playwright config for the consumer smoke spec.
//
// Run via `npm run test:e2e`. The smoke is intentionally narrow: a
// single happy-path flow over the consumer surfaces (Feed → Live
// modal-cover → AskAvatar modal-cover) so a regression in the
// modal-cover semantics, the feed pager, or the consumer route group
// surfaces immediately. The smoke runs in a separate CI
// step from the unit suite (`npm test`) so unit failures don't block
// the longer end-to-end run, and end-to-end failures don't gate
// per-PR review on the unit gate.
//
// The app is open access; sign-in is not exercised in the smoke and the
// consumer routes are publicly reachable.
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PLAYWRIGHT_PORT ?? 3100);
const HOST = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // Smoke is one happy-path test; no need to parallelize and the dev
  // server only handles one connection well.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  // Reasonable for a hand-rolled feed pager: the IO observer plus the
  // feed-session POST can take a moment to settle on cold start.
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: {
    baseURL: HOST,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 } },
    },
  ],
  webServer: {
    command: `npm run dev -- -p ${PORT}`,
    url: HOST,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      // Point at a local server stub if one is running; otherwise the
      // page renders the loading shimmer and the smoke can still
      // exercise the navigation chrome.
      NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000",
    },
  },
});
