# Disclosure policy

A live-selling experience where the hosts are AI and the pitches are paid
has real disclosure obligations. This document describes those obligations
and how this demo surfaces them through the public feed API, so the pattern
is clear if you build on it. It is the source of truth for what each
disclosure flag means and why it exists.

## Why disclosure matters

Two FTC-relevant facts apply to every feed slot in this demo:

1. **Creators are AI.** Every persona here is AI-generated, not a human
   creator. The FTC's Endorsement Guides treat material connections
   between an endorser and an advertiser, including the fact that the
   endorser is not human, as facts a reasonable consumer would want to
   know.
2. **Pitches are paid placements.** When a persona promotes a product,
   that product is part of a paid campaign. Paid endorsements must be
   disclosed clearly and conspicuously.

The server is the source of truth for both facts. A client cannot infer
them from other fields without coupling itself to the campaign schema, so
the API exposes them as explicit booleans.

## Flags returned by `/feed/cursor`

### `FeedItemOut.is_ad: bool`

- `true` when the slot's `product_id` is not null, meaning the persona is
  pitching a product from an active campaign.
- `false` for freestyle or comedy slots that have no product attached.
- Required: yes. The client renders an `(Ad)` chip whenever this is
  `true`.

### `PersonaOut.is_ai_creator: bool`

- `true` for every persona in this demo (all the hosts are AI).
- The flag lives on the persona, not the feed slot, so the disclosure
  travels with the entity if it appears outside the feed (profile
  pages, share sheets, and so on).
- Required: yes. The client renders an AI-creator disclosure whenever
  this is `true`.

## Client obligations

A client should render both disclosures whenever the corresponding flag is
`true`. Disclosures should be visible without user interaction (no taps,
expansions, or scrolls required) and should remain on screen for the
duration the slot is shown.

## Changing this policy

The flags are part of the public API contract, so removing one or changing
its default is a breaking change. More to the point, any change that
weakens a disclosure is exactly what a real deployment would want to review
carefully, since these flags are what keep the feed honest about AI hosts
and paid placements.
