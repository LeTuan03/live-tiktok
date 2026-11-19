# Security Policy

SoShop is a demo, but it integrates real provider credentials, so we take security reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's private vulnerability reporting:

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Describe the issue, the impact, and the steps to reproduce.

If you cannot use GitHub's flow, contact a maintainer directly and ask for a private channel before sharing details.

We will acknowledge a valid report, work with you on a fix, and credit you when it lands unless you prefer to stay anonymous.

## Scope

This repository is reference code. When you run it, **you** supply the secrets and own the deployment. A few things to keep in mind:

- **Secrets belong in a git-ignored `.env`.** Only the `.env.example` files are committed. Never commit a real key, and rotate anything that lands in git history by mistake.
- **The app runs open access.** There is no external auth provider. The consumer feed is public by design. Do not assume any route authenticates the caller.
- **The seed data and provider IDs are illustrative.** Replace them with your own before deploying.

## What is out of scope

- Vulnerabilities in third-party providers (LiveKit, LemonSlice, ElevenLabs, Groq, Neon, Tigris). Report those to the provider.
- Findings that require committing your own secrets or misconfiguring your own deployment.
