# web/: SoShop consumer feed

Next.js 15 (App Router) on React 19. Tailwind for styling, the LiveKit client for live rooms, Vitest + Playwright for tests. Deploys to Vercel. The app runs open access, with no external auth provider (see [Access model](#access-model)).

## Commands

```bash
cp .env.example .env.local        # fill in NEXT_PUBLIC_API_BASE_URL
npm install
npm run dev                       # next dev
npm run build                     # next build
npm run lint                      # biome lint
npm run format                    # biome format --write
npm run check                     # biome check (lint + format; CI runs `biome ci`)
npm run typecheck                 # tsc --noEmit
npm test                          # vitest run (one-shot, used in CI)
npm run test:watch
npm run test:e2e:install          # playwright install --with-deps chromium (one-time)
npm run test:e2e                  # playwright test
```

## Code quality (Biome)

[Biome](https://biomejs.dev) is the linter and formatter (`biome.json`); it replaces `next lint` (there is no ESLint or Prettier config). Use `npm run lint`, `npm run format`, and `npm run check`; CI runs `biome ci`. The formatter is 2-space indent, double quotes, 100-col. A few rules are deliberately relaxed for this demo: `noImgElement` (the feed uses `<img>`), `noNonNullAssertion`, and `useExhaustiveDependencies` are off, and Tailwind's at-rules (`noUnknownAtRules`) are off. The a11y rules are set to **warn** so they surface without blocking CI. Generated files (`lib/api/generated/`, `styles/tokens.css`) are excluded.

## Committed artifacts: `tokens.css` and `lib/api/generated/`

Two artifacts in `web/` are generated and committed to git so Vercel can build with pure Node and npm, no `uv` and no Python:

- `styles/tokens.css` is compiled from `lib/branding/colors.json` (the canonical brand tokens) via `npm run build:tokens` (`scripts/build-tokens.mjs`). `tailwind.config.ts` reads the same JSON.
- `lib/api/generated/` is the typed client generated from the server's OpenAPI schema via `npm run gen:api`.

There are no `prebuild` / `predev` / `postinstall` hooks. `next build` runs straight from the committed artifacts.

When you change a brand color, edit `lib/branding/colors.json` and run `npm run build:tokens`, then commit `styles/tokens.css`. When the server's API surface changes, run `npm run gen:api` and commit `lib/api/generated/`.

### `gen:api` internals

1. `gen:api:spec` calls `server/scripts/dump_openapi.py` (via `uv run`) to write `server/openapi.json` straight from the FastAPI app's `app.openapi()` snapshot. No network round-trip.
2. `gen:api:client` runs `@hey-api/openapi-ts` against that spec, emitting `lib/api/generated/{client.gen.ts,sdk.gen.ts,types.gen.ts,index.ts}`.

`server/openapi.json` is git-ignored. It is a transient intermediate artifact. Only the final TypeScript SDK is committed.

Treat the generated files as read-only. If you need to change a wire shape, change the Pydantic model on the server, then re-run `gen:api` and commit the regen.

### Using the generated SDK

`lib/api/sdk.ts` wires the generated client to `NEXT_PUBLIC_API_BASE_URL` and adapts thrown SDK errors into the canonical `ApiError`. Usage:

```ts
import { listChannelsChannelsGet } from "@/lib/api/generated";
import { sdkOptions, adaptSdkError } from "@/lib/api/sdk";

try {
  const { data } = await listChannelsChannelsGet(sdkOptions());
  return data; // typed off the Pydantic model
} catch (err) {
  throw await adaptSdkError(err);
}
```

Calls need no auth token. The app runs open access (see [Access model](#access-model)); user-scoped requests fall back to a demo user on the server.

### Legacy hand-mirrored clients

`lib/api/{channels,persona}.ts` are older hand-mirrored clients. They still work and remain the canonical call sites until they are migrated to the generated SDK one at a time. New endpoints should use the generated SDK directly. Migrating the existing ones is a follow-up.

When migrating, keep the camelCase domain types and `adapt*` helpers. Generated types are snake_case-shaped (they mirror the Pydantic wire format), and a wire-to-domain adapter is still the right place to land field renames.

## Layout

```
app/
├── layout.tsx
├── globals.css                   # Tailwind entry
└── (consumer)/                   # PUBLIC consumer feed
    ├── /, /persona, /favorites, /customize, ...
    ├── /studio                   # studio: products, Flow videos (/videos), sessions (/live)
    └── /onair/[id]               # 9:16 on-air screen for viewers and OBS
components/                       # *.test.tsx co-located with source
lib/                              # analytics, favorites, feed, persona clients
e2e/                              # Playwright specs
__tests__/                        # cross-cutting Vitest specs
```

Path alias: `@/*` resolves to the package root (see `tsconfig.json`). Prefer `@/components/...` over relative climbs.

## Access model

The app runs open access. There is no external auth provider, no middleware, and no client-side gate.

The consumer surface (`/`, `/persona`, `/favorites`, `/customize`, ...) is public; server components render anonymously.

Identity is resolved server-side. User-scoped requests (favorites, feed events, chat) resolve from an optional `X-User-Id` header and fall back to a stable demo user. SDK calls need no auth token.

## Testing conventions

- **Unit tests in Vitest** with the jsdom environment and React Testing Library. Setup is `vitest.setup.ts`.
- **LiveKit is stubbed at the SDK boundary.** No test should open a real LiveKit room. Mock `livekit-client` imports rather than running real connections.
- **Test files live next to the code** as `*.test.ts(x)`. Cross-cutting tests go under `__tests__/`.
- **E2E in Playwright** under `e2e/`. These run against a built app and are NOT part of `npm test`. Run them separately, and run `test:e2e:install` once to fetch the browser.

## Deploy

Push to `main` for a Vercel preview or production deploy. Env vars (`NEXT_PUBLIC_API_BASE_URL`) are set in the Vercel project, not committed.
