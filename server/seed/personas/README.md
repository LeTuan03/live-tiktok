# SoShop persona seed data

*Part of the [SoShop monorepo](../../../README.md). See the [server README](../../README.md) for database and environment setup, and the root README's [Add or swap a host](../../../README.md#add-or-swap-a-host) for putting a host on a live channel.*

This directory holds the AI seller personas, and (one level up) the product catalog they pitch, that seed a SoShop environment.

Following the `.env` convention, the **real** data is git-ignored and the repo commits brand-free **`*.example`** templates instead:

- `personas/demo-host.json.example`: the committed demo host, "Leila".
- `../catalog.json.example`: the demo advertiser "Aurora Goods Co.", 10 products (`LE-001`..`LE-010`), and a campaign, with `picsum.photos` placeholder images.

The seeder (`server/scripts/seed_v1.py`) prefers your real files when present (`catalog.json`, `personas/*.json`) and falls back to the `*.example` templates otherwise, so a fresh clone seeds and runs out of the box. To use your own data, copy a template (e.g. `cp ../catalog.json.example ../catalog.json`, `cp personas/demo-host.json.example personas/my-host.json`), edit it, and re-seed. Your real files stay git-ignored. The loader is idempotent (deterministic UUID5 + `ON CONFLICT DO UPDATE`).

> The committed demo host and catalog are illustrative and brand-free. Swap in your own catalog, avatars, and voices.

## New host at a glance

1. **Catalog first.** Add the products you'll pitch to `../catalog.json` (copy `../catalog.json.example` if you don't have a real catalog yet). Every script's `product_sku` must resolve here, or the seeder raises.
2. **Copy a template.** `cp demo-host.json.example my-host.json`, then fill in every field in the [schema](#json-schema) below.
3. **Author the pitches.** Write **at least 10** scripts, each with a real `product_sku` and a hook + body + cta in the **30 to 45 word** band.
4. **Validate.** `cd server && uv run pytest tests/seed` clears both seed gates (the word band, and the SKU + per-host floor checks).
5. **Re-seed Postgres.** `cd server && DATABASE_URL=postgresql://... uv run python scripts/seed_v1.py`.

Dropping a JSON file in alone changes nothing: the running agent loads hosts from Postgres, so step 5 is what registers your host and rebuilds the rotation. See the root README's [Add or swap a host](../../../README.md#add-or-swap-a-host) for putting it on a live channel.

## Authoring contract: word count

Each script's **combined spoken footprint** (hook + `script_text` + cta) must land in **30 to 45 words**. At ElevenLabs' default of roughly 150 WPM (about 2.5 words per second), that is 12 to 18 seconds of audio, centered near 15. The band gives natural variation room.

The channel rotation does not run on a fixed timer. It swaps to the next persona the instant the current pitch finishes speaking. There is no hard cutoff, so pace organically. Pitches that drift past the band start crowding the cadence, and pitches that come in short waste the slot. The validator catches drift on either side.

| Band | Why |
|---|---|
| min 30 words | about 12 s; below this the slot feels rushed |
| target 37 | about 15 s; the natural rotation cadence |
| max 45 words | about 18 s; above this you start crowding the next persona |

## JSON schema

```json
{
  "slug": "kebab-case-slug",
  "name": "Display Name",
  "avatar_image_url": "https://...",
  "voice_id": "elevenlabs-voice-id",
  "voice_pipeline_id": "elevenlabs-eleven_turbo_v2_5",
  "persona_kind": "host|guest|expert|celebrity",
  "status": "active",
  "traits": "comedic|expert|evergreen",
  "personality": "1-3 sentence sketch",
  "backstory": "2-3 sentence character history",
  "scripts": [
    {
      "kind": "pitch",
      "product_sku": "LE-001",
      "script_text": "The pitch body the LLM paraphrases.",
      "hook": "Opening line, one short beat.",
      "cta": "Verbatim call to action, delivered word for word.",
      "weight": 1
    }
  ]
}
```

- `product_sku` **must** exist in the catalog (`catalog.json`, or `catalog.json.example` on a fresh clone). The seeder raises on an unknown SKU, and `tests/seed/test_per_persona_ad_count.py` enforces it (plus a floor of 10 scripts per persona).
- `cta` is delivered **verbatim** by the LLM. The persona prompt forbids paraphrasing it. Keep CTAs tight (5 to 9 words).
- `hook` is the opening beat. 6 to 13 words is typical.
- `script_text` is the body. The LLM may paraphrase it, so word choice should fit the persona's voice. 18 to 28 words is the sweet spot.
- `weight` biases multi-script selection when more than one script per persona maps to the same product (higher means more often).

## Validating

Before re-seeding, run the seed gates:

```bash
cd server
uv run pytest tests/seed
```

`test_persona_word_count.py` checks the 30 to 45 band; `test_per_persona_ad_count.py` checks the per-persona script floor and that every SKU resolves. A failure prints the persona slug, script index, product SKU, and word count, so you can find the offending entry in seconds.

## Re-seeding the DB

```bash
cd server
DATABASE_URL=postgresql://... uv run python scripts/seed_v1.py
```

The seeder loads the catalog (`catalog.json`, else `catalog.json.example`) and every persona (`personas/*.json`, else `*.example`), then upserts advertisers, products, media, campaigns, personas, and scripts, rebuilds the per-persona ad rotation, and binds personas to channels. It is idempotent: rows are keyed by UUID5, so re-running after an edit updates in place without duplicating. Personas and scripts no longer present in the JSON are pruned.

## Voice notes

Each persona has a distinct voice. When trimming a pitch, **cut filler, not character**. The committed demo host, Leila, is warm and centered, the instructor who has tested everything and only recommends what survives her own ten-class week. Flattening the voice to hit the word count is worse than a slight word-count overshoot.
