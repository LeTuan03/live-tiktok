# SoShop

A demo of AI-hosted live shopping, built on LemonSlice. Two independent subsystems share one repo.

| Subsystem | Path | Stack | Has its own `CLAUDE.md` |
|---|---|---|---|
| Backend API + LiveKit agent | `server/` | Python 3.11, FastAPI, asyncpg, uv | yes |
| Consumer feed | `web/` | Next.js 15 (App Router), Tailwind | yes |

`desktop/` packages both into a Windows installer (`SoShop-Setup.exe`, a C# launcher that runs Postgres, LiveKit, the API, the worker and the web app as plain processes). It has no code of its own beyond packaging; see `desktop/README.md`. If you change how the server or web app is started or configured (env vars, ports, commands), check `desktop/launcher/Stack.cs` as well as `docker-compose.yml`.

The two subsystems are **not** a Yarn/pnpm/Cargo workspace. There is no root `package.json` or `pyproject.toml`. Build, test, and CI run independently per subsystem. Pick the directory you are working in and use *its* `CLAUDE.md` for commands and conventions.

## Brand tokens

`web/lib/branding/colors.json` is the canonical brand palette for the web app. It compiles to `web/styles/tokens.css` (CSS custom properties) via `web/scripts/build-tokens.mjs`, and `web/tailwind.config.ts` reads the same JSON. To change a brand color, edit that JSON and run `npm run build:tokens` in `web/`, then commit the regenerated `tokens.css`.

## Things that surprise people

- **Open access, no auth provider.** The app runs without an external auth provider. The consumer feed at `/` is public, and the server falls back to a demo user for user-scoped routes.
- **Hermetic tests by default.** Server integration tests against a real database or S3 only run when `DATABASE_URL` / `BUCKET_NAME` are set. The default `uv run pytest` hits no external services.
- **In-place persona swap.** Swiping in the feed mutates the *running* LiveKit agent via control messages. It does not redispatch the room.
- **Livestream sessions derive their position.** `/studio/live` sessions play pre-rendered videos. The server stores only a playhead anchor and the server and the web player both compute what is on air from it with the same algorithm (`server/src/soshop/broadcast/timeline.py`, `web/lib/broadcast/timeline.ts`). Change one, change both.
- **Completion-driven channel rotation.** The always-on channels swap personas off the agent reply's `SpeechHandle` rather than a wall-clock timer, so swaps land the moment a pitch finishes speaking.

## Conventions

- Touch one subsystem at a time. Use its `CLAUDE.md` for the conventions that apply there.
- Let the linters be the style guide (Ruff for `server/`, the Next.js and TypeScript tooling for `web/`).
- Keep secrets out of git. `server/` reads a git-ignored `.env` (pydantic Settings), `web/` reads a git-ignored `.env.local` (Next.js). Only the `.env.example` files are committed.
- No em dashes in prose. Commas and periods do the job.
