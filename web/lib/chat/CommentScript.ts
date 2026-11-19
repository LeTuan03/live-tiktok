// Pure script generator. Given a product, returns a deterministic
// sequence of `ScriptedTick`s — one per simulated comment — spanning
// `durationMs` (default 30 s). The simulator schedules these via
// setTimeout off a single anchor time.
//
// The density profile is front-loaded to mirror real live-shopping
// engagement: research shows the first moment of a pitch sets the
// engagement tone (see e.g. comment activity as the strongest signal
// of live-commerce conversion). Concretely:
//
//   0–5 s   : "opening hook"   — dense (avg ≈ 0.6 s gap, 8 messages)
//   5–15 s  : "demo / peak"    — moderate (avg ≈ 1.3 s gap, 7 messages)
//   15–30 s : "wind-down"      — sparse (avg ≈ 2.6 s gap, 6 messages)
//
// Group-excitement bursts: the script picks 1–2 windows where three
// consecutive comments share a theme (drawn from `BURST_FAMILIES`).
// Bursts simulate the room reacting to the same on-screen moment and
// trigger in-group identification — Cialdini-style social proof on a
// micro-timescale.
//
// Determinism: the generator is seeded by a hash of `productId`, so
// every viewer (and every re-mount on the same product) sees the same
// scripted stream. This is intentional — when one viewer talks about
// "the colorway" you want all viewers to see the same reaction beats
// from neighbours, like a curated demo room. Swap to a real wire when
// the global chat fan-out exists; the seed contract preserves the
// "single deterministic room" feel until then.

import {
  BURST_FAMILIES,
  CHAT_USERNAMES,
  HYPE_LINES,
  QUESTION_LINES,
  REACTION_LINES,
  SOCIAL_PROOF_LINES,
} from "./CommentCatalog";

export interface ScriptedTick {
  /** Ms offset from the script anchor (i.e. when the simulator started). */
  atMs: number;
  authorHandle: string;
  text: string;
}

export interface BuildScriptOptions {
  productId: string;
  /** Total script length. Defaults to 30 s so the stream covers the
   *  ~15 s average pitch plus a tail for the inter-pitch gap. */
  durationMs?: number;
}

const DEFAULT_DURATION_MS = 30_000;

interface PhaseSpec {
  /** Phase start ms (inclusive). */
  startMs: number;
  /** Phase end ms (exclusive). */
  endMs: number;
  /** Total messages this phase should emit. */
  count: number;
  /** Pool weights — sum doesn't need to be 1; we normalise. */
  weights: Partial<Record<PoolKey, number>>;
}

type PoolKey = "hype" | "question" | "social" | "reaction";

const POOLS: Record<PoolKey, readonly string[]> = {
  hype: HYPE_LINES,
  question: QUESTION_LINES,
  social: SOCIAL_PROOF_LINES,
  reaction: REACTION_LINES,
};

// Phase shape:
//   Opening — heavy hype + emoji reactions, almost no questions yet
//   Peak    — balanced; questions show up as viewers commit to buying
//   Tail    — social-proof closes the loop ("third one I'm snagging")
function defaultPhases(durationMs: number): PhaseSpec[] {
  const openingEnd = Math.min(5_000, durationMs);
  const peakEnd = Math.min(15_000, durationMs);
  return [
    {
      startMs: 0,
      endMs: openingEnd,
      count: 8,
      weights: { hype: 0.55, reaction: 0.35, social: 0.1 },
    },
    {
      startMs: openingEnd,
      endMs: peakEnd,
      count: 7,
      weights: { hype: 0.3, question: 0.3, reaction: 0.2, social: 0.2 },
    },
    {
      startMs: peakEnd,
      endMs: durationMs,
      count: 6,
      weights: { social: 0.45, reaction: 0.25, question: 0.2, hype: 0.1 },
    },
  ];
}

/**
 * Build the full per-product script.
 *
 * Pure — given the same input (and the same catalog) the output is
 * byte-identical. Tests use this property to lock down density
 * boundaries and burst placement without faking timers.
 */
export function buildScript(options: BuildScriptOptions): ScriptedTick[] {
  const durationMs = options.durationMs ?? DEFAULT_DURATION_MS;
  const rng = mulberry32(hashString(options.productId));
  const phases = defaultPhases(durationMs);

  const ticks: ScriptedTick[] = [];
  for (const phase of phases) {
    appendPhaseTicks(ticks, phase, rng);
  }

  // Group-excitement bursts. Stitch 1–2 bursts into the opening + peak
  // by overwriting three consecutive ticks with a burst-family triple.
  // Choosing positions deterministically keeps the burst visible in the
  // first 5–15 s window — that's where attention is and where a burst
  // has the strongest social-proof payoff.
  injectBurst(ticks, rng, { afterMs: 2_000, beforeMs: 7_000 });
  injectBurst(ticks, rng, { afterMs: 9_000, beforeMs: 14_000 });

  // Stable chronological order — `appendPhaseTicks` already emits each
  // phase in order, but explicit sort guards against burst rewrites
  // shuffling neighbour timing if we ever add jitter on overwrite.
  ticks.sort((a, b) => a.atMs - b.atMs);

  return ticks;
}

function appendPhaseTicks(out: ScriptedTick[], phase: PhaseSpec, rng: () => number): void {
  const span = phase.endMs - phase.startMs;
  if (span <= 0 || phase.count <= 0) return;

  // Distribute timestamps inside the phase with mild jitter so the
  // cadence reads as organic rather than metronomic. Each tick lands
  // inside its even-step bucket plus a ±35% random offset.
  const step = span / phase.count;
  for (let i = 0; i < phase.count; i++) {
    const center = phase.startMs + step * (i + 0.5);
    const jitter = (rng() - 0.5) * step * 0.7;
    const atMs = clamp(Math.round(center + jitter), phase.startMs, phase.endMs - 1);
    const poolKey = pickPool(phase.weights, rng);
    const text = pickFromPool(POOLS[poolKey], rng);
    const author = pickAuthor(rng);
    out.push({ atMs, authorHandle: author, text });
  }
}

function injectBurst(
  ticks: ScriptedTick[],
  rng: () => number,
  range: { afterMs: number; beforeMs: number },
): void {
  // Find three consecutive ticks falling inside the target window. If
  // we can't (script too short, window outside range) skip — the rest
  // of the script is still valid.
  let startIdx = -1;
  for (let i = 0; i < ticks.length - 2; i++) {
    const a = ticks[i];
    const b = ticks[i + 2];
    if (a.atMs >= range.afterMs && b.atMs <= range.beforeMs) {
      startIdx = i;
      break;
    }
  }
  if (startIdx < 0) return;

  const family = BURST_FAMILIES[Math.floor(rng() * BURST_FAMILIES.length)];
  for (let k = 0; k < 3; k++) {
    ticks[startIdx + k] = {
      ...ticks[startIdx + k],
      text: family[k],
      // Re-pick the author so the three burst lines come from three
      // distinct viewers — the "group" feeling depends on multiple
      // people, not one user spamming.
      authorHandle: pickAuthor(rng),
    };
  }
}

function pickPool(weights: Partial<Record<PoolKey, number>>, rng: () => number): PoolKey {
  const entries = Object.entries(weights) as [PoolKey, number][];
  const total = entries.reduce((acc, [, w]) => acc + w, 0);
  let pick = rng() * total;
  for (const [key, w] of entries) {
    pick -= w;
    if (pick <= 0) return key;
  }
  return entries[entries.length - 1][0];
}

function pickFromPool(pool: readonly string[], rng: () => number): string {
  return pool[Math.floor(rng() * pool.length)];
}

function pickAuthor(rng: () => number): string {
  return CHAT_USERNAMES[Math.floor(rng() * CHAT_USERNAMES.length)];
}

function clamp(n: number, lo: number, hi: number): number {
  return n < lo ? lo : n > hi ? hi : n;
}

/** FNV-1a 32-bit string hash. Tiny + good enough for seeding. */
function hashString(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Mulberry32 — 32-bit PRNG, deterministic per seed. */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
