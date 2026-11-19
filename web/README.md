# soshop-web

The consumer feed for the SoShop live-selling demo.

Stack: Next.js 15 (App Router) on React 19, Tailwind CSS, and the LiveKit client for live rooms. Linted and formatted with Biome, tested with Vitest and Playwright. Deploys to Vercel.

*Part of the [SoShop monorepo](../README.md). See [`CLAUDE.md`](CLAUDE.md) for in-repo coding conventions and the access model.*

## Local development

```bash
cp .env.example .env.local   # fill in NEXT_PUBLIC_API_BASE_URL
npm install
npm run dev                  # next dev
```

| Task | Command |
|---|---|
| Lint (Biome) | `npm run lint` |
| Format (Biome) | `npm run format` |
| Lint + format check | `npm run check` |
| Type check | `npm run typecheck` |
| Unit / component tests | `npm test` |
| End-to-end (Playwright) | `npm run test:e2e:install` then `npm run test:e2e` |
| Production build | `npm run build` |

The brand tokens and the typed API client are committed artifacts, so `npm run dev` is a plain `next dev` with no codegen step in front of it (see [Generated artifacts](#generated-artifacts)).

## Tests

Component and API-client tests run in Vitest with the jsdom environment and React Testing Library. LiveKit interactions are stubbed at the SDK boundary, so no real LiveKit room is contacted during unit tests.

```bash
npm test           # one-shot run (used in CI)
npm run test:watch # interactive watch mode
```

Test files live alongside the code as `*.test.ts(x)` under `components/` and `app/`. Cross-cutting specs live under `__tests__/`. End-to-end tests run in Playwright under `e2e/` and are **not** part of `npm test`. Run `npm run test:e2e:install` once, then `npm run test:e2e`.

## Environment

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | Base URL of the SoShop server API (`server/`) |
| `SITE_PASSWORD` | Bearer token the `/api/agents/session` proxy forwards to the server's `/agents/session` route (the 1:1 "talk to the host" flow). Must match `SITE_PASSWORD` on the server. |
| `NEXT_PUBLIC_PERSONA_DEFAULT_ID` / `NEXT_PUBLIC_PERSONA_DEFAULT_SCRIPT_ID` | Optional default persona/script for the `/persona` companion route. Falls back to a built-in `"default"` id when unset. |

## Generated artifacts

Two derived files are **committed to git** so Vercel can build with plain Node, no `uv` and no Python:

- `styles/tokens.css` is compiled from `lib/branding/colors.json` (the canonical brand tokens) by `scripts/build-tokens.mjs` (`npm run build:tokens`).
- `lib/api/generated/` is the typed client generated from the server's OpenAPI schema (`npm run gen:api`, which shells out to `uv run python` in `../server/`).

When you change a brand color, edit `lib/branding/colors.json` and run `npm run build:tokens`. When the server's API surface changes, run `npm run gen:api`. Either way, commit the regenerated output.

## Routing and access model

The app runs open access. There is no external auth provider, no middleware, and no client-side stubs. The consumer surface lives at `/` (the swipe feed), `/persona` (a 1:1 "talk to the host" companion session), `/customize` (build your own host: upload an avatar image, pick an ElevenLabs voice, and preview it live), and `/favorites`. All publicly reachable.

Identity is resolved server-side: user-scoped requests (favorites, feed events) fall back to a stable demo user, overridable via an optional `X-User-Id` header. See [`CLAUDE.md`](CLAUDE.md) for the full access model.

## Deploy

Push to `main` to trigger a Vercel preview or production deploy. Set the env variables above in the Vercel project settings.
