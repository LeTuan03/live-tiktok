// React 19 + React Testing Library require `NODE_ENV` to be `test` (or any
// non-production value) at the moment React Testing Library is imported —
// `act()` is a no-op in production builds and silently swallows test-side
// state updates. Force the env here, before any React imports happen at
// config-evaluation time, so the suite stays hermetic regardless of the
// surrounding shell.
// Cast required because @types/node declares `NODE_ENV` as a readonly
// branded literal union; the runtime assignment is fine because vitest
// hasn't imported React yet.
(process.env as Record<string, string>).NODE_ENV = "test";

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
    css: false,
    include: [
      "components/**/*.test.{ts,tsx}",
      "app/**/*.test.{ts,tsx}",
      "lib/**/*.test.{ts,tsx}",
      // The cross-client envelope-parity suite lives at the repo
      // top level (`web/__tests__/`) per the issue spec — keep it
      // runnable by including the directory here.
      "__tests__/**/*.test.{ts,tsx}",
    ],
  },
});
