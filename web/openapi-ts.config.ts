// `@hey-api/openapi-ts` configuration.
//
// Reads `../server/openapi.json` (produced by
// `server/scripts/dump_openapi.py`) and emits a typed TS SDK +
// `@hey-api/client-fetch` SDK methods under `lib/api/generated/`.
//
// Regenerate after any server route change:
//
//   npm run gen:api       # one-step: dumps spec, then generates client
//
// The generated files are committed to git, so Vercel builds with pure
// Node and npm (no Python step). There are no predev / prebuild hooks:
// regenerate manually with `npm run gen:api` after a server route change
// and commit the result. Treat them as read-only; if you need to change
// wire shapes, change the Pydantic model and re-run `gen:api`.
//
// Output layout (hey-api defaults):
//   lib/api/generated/
//     ├── client.gen.ts        # configured fetch client
//     ├── sdk.gen.ts           # one async function per server operation
//     ├── types.gen.ts         # request/response TS types
//     └── index.ts             # re-exports the above

import { defineConfig } from "@hey-api/openapi-ts";

export default defineConfig({
  input: "../server/openapi.json",
  // Prettier isn't a project dep — we rely on Next's lint config not
  // enforcing formatting on the generated dir. If you wire prettier
  // in, swap this for `{ path, format: "prettier" }`.
  output: "lib/api/generated",
  plugins: [
    {
      name: "@hey-api/client-fetch",
      // Base URL is configured at call time (we already use
      // NEXT_PUBLIC_API_BASE_URL via authedFetch), so leave
      // runtimeConfigPath unset.
    },
    "@hey-api/typescript",
    "@hey-api/sdk",
  ],
});
