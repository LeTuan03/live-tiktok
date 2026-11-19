# Contributing to SoShop

Thanks for taking the time to look. SoShop is a demo of AI-hosted live selling built on LemonSlice, so the goal of any change is to make the example clearer, more correct, or more useful to someone evaluating the stack. Production features are not the target here.

Good contributions include:

- Bug fixes in the agent, feed, or API.
- Clearer docs, comments, and setup instructions.
- Better local-dev ergonomics (one fewer step to a running demo).
- Swapping fragile or environment-specific bits for portable defaults.

## Before you start

This repo is two independent subsystems sharing one directory. Work in one at a time:

- [`server/`](server/): Python 3.11, FastAPI, the LiveKit agent. Tooling is [`uv`](https://docs.astral.sh/uv/) and Ruff. See [`server/CLAUDE.md`](server/CLAUDE.md).
- [`web/`](web/): Next.js 15, React 19, the consumer feed. See [`web/CLAUDE.md`](web/CLAUDE.md).

Each has its own `.env.example`, its own tests, and its own CI workflow. There is no root build.

## Local setup

Follow the quick start in the [README](README.md). The short version:

```bash
# server
cd server && cp .env.example .env && uv sync
uv run uvicorn soshop_server.api.app:app --port 8000

# web (separate terminal)
cd web && cp .env.example .env.local && npm install && npm run dev
```

## Before you open a pull request

Run the checks for whichever subsystem you touched. CI runs the same ones.

**server/**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

**web/**

```bash
npm run lint
npm run typecheck
npm test
```

Keep the diff focused. A pull request that fixes one thing and explains why is easier to merge than a large refactor. If you are changing behavior, update the relevant `README.md` or `CLAUDE.md` in the same pull request.

## A few house rules

- **Never commit secrets.** Every subsystem reads from a git-ignored `.env`. Only the `.env.example` files belong in git. If you add a new config value, add it to the example with a placeholder, not a real key.
- **Let the linters decide style.** Do not hand-format around Ruff or the TypeScript tooling.
- **Database changes ship as numbered SQL.** Add a `migrations/000N_<name>.up.sql` and its matching `.down.sql`. There is no ORM and no migration framework.
- **No em dashes in prose.** It is a small house style. Commas and periods do the job.

## Reporting bugs and proposing changes

Open an issue using one of the templates. For anything security-sensitive, do not open a public issue. Follow [SECURITY.md](SECURITY.md) instead.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
