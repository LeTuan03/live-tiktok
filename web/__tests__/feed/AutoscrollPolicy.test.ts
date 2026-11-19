import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AutoscrollPolicy,
  DEFAULT_GRACE_MS,
  DEFAULT_SAFETY_CAP_MS,
} from "@/lib/feed/AutoscrollPolicy";

interface Harness {
  policy: AutoscrollPolicy;
  advance: ReturnType<typeof vi.fn>;
  advanceClock: (ms: number) => void;
}

function makeHarness(
  graceMs: number = DEFAULT_GRACE_MS,
  safetyCapMs: number = DEFAULT_SAFETY_CAP_MS,
): Harness {
  const advance = vi.fn();
  const policy = new AutoscrollPolicy({
    graceMs,
    safetyCapMs,
    requestAdvance: advance,
  });
  return {
    policy,
    advance,
    advanceClock: (ms: number) => vi.advanceTimersByTime(ms),
  };
}

describe("AutoscrollPolicy", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("Rule 1: pitchEnd with no interaction advances after grace delay", () => {
    const h = makeHarness(1500);
    h.policy.cardActivated();
    h.policy.pitchEnd();
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(1499);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(2);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("Rule 2: interaction before pitchEnd suppresses autoscroll on completion", () => {
    const h = makeHarness(1500, 60_000);
    h.policy.cardActivated();
    h.policy.interacted("favorite");
    h.policy.pitchEnd();
    h.advanceClock(10_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("Rule 2: interaction during the grace window cancels the grace timer", () => {
    const h = makeHarness(1500, 60_000);
    h.policy.cardActivated();
    h.policy.pitchEnd();
    h.advanceClock(500);
    h.policy.interacted("chat");
    h.advanceClock(2_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("Safety cap: post-interaction advance fires after safetyCapMs of no further interaction", () => {
    const h = makeHarness(1500, 60_000);
    h.policy.cardActivated();
    h.policy.interacted("product_tap");
    h.advanceClock(59_999);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(2);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("Safety cap is reset by further interactions", () => {
    const h = makeHarness(1500, 60_000);
    h.policy.cardActivated();
    h.policy.interacted("favorite");
    h.advanceClock(45_000);
    h.policy.interacted("chat");
    h.advanceClock(45_000);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(20_000);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("userScrolled cancels a pending grace advance", () => {
    const h = makeHarness(1500);
    h.policy.cardActivated();
    h.policy.pitchEnd();
    h.advanceClock(500);
    h.policy.userScrolled();
    h.advanceClock(5_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("userScrolled cancels a pending safety-cap advance", () => {
    const h = makeHarness(1500, 60_000);
    h.policy.cardActivated();
    h.policy.interacted("buy");
    h.advanceClock(30_000);
    h.policy.userScrolled();
    h.advanceClock(60_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("cardDeactivated cancels every pending timer and never fires advance", () => {
    const h = makeHarness();
    h.policy.cardActivated();
    h.policy.pitchEnd();
    h.policy.cardDeactivated();
    h.advanceClock(60_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("requestAdvance is called at most once per policy lifetime", () => {
    const h = makeHarness(100);
    h.policy.cardActivated();
    h.policy.pitchEnd();
    h.advanceClock(200);
    h.policy.pitchEnd();
    h.policy.interacted("chat");
    h.advanceClock(120_000);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("cardActivated is idempotent (no extra timers, no extra advances)", () => {
    const h = makeHarness(1500);
    h.policy.cardActivated();
    h.policy.cardActivated();
    h.policy.pitchEnd();
    h.advanceClock(2000);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("pitchEnd before cardActivated is ignored (idle ignores events)", () => {
    const h = makeHarness(1500);
    h.policy.pitchEnd();
    h.advanceClock(5000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("interaction after pitchEnd cancels grace and arms safety cap", () => {
    const h = makeHarness(1500, 60_000);
    h.policy.cardActivated();
    h.policy.pitchEnd();
    h.advanceClock(800);
    h.policy.interacted("chat");
    h.advanceClock(59_999);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(2);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("onTransition is called for each state change", () => {
    const transitions: string[] = [];
    const advance = vi.fn();
    const policy = new AutoscrollPolicy({
      graceMs: 100,
      safetyCapMs: 1000,
      requestAdvance: advance,
      onTransition: (s) => transitions.push(s.kind),
    });
    policy.cardActivated();
    policy.pitchEnd();
    vi.advanceTimersByTime(200);
    expect(transitions).toEqual(["watching", "pendingAdvance", "cancelled"]);
  });
});
