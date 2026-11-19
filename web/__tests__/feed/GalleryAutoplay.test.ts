import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GalleryAutoplay, DEFAULT_GALLERY_AUTOPLAY_MS } from "@/lib/feed/GalleryAutoplay";

interface Harness {
  autoplay: GalleryAutoplay;
  advance: ReturnType<typeof vi.fn>;
  advanceClock: (ms: number) => void;
}

function makeHarness(intervalMs: number = DEFAULT_GALLERY_AUTOPLAY_MS): Harness {
  const advance = vi.fn();
  const autoplay = new GalleryAutoplay({ intervalMs, advance });
  return {
    autoplay,
    advance,
    advanceClock: (ms: number) => vi.advanceTimersByTime(ms),
  };
}

describe("GalleryAutoplay", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("advances once per interval after start", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(4999);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(2);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("loops: keeps advancing every interval", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.advanceClock(15_000);
    expect(h.advance).toHaveBeenCalledTimes(3);
  });

  it("default dwell is 5s", () => {
    const h = makeHarness();
    h.autoplay.start();
    h.advanceClock(DEFAULT_GALLERY_AUTOPLAY_MS - 1);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(1);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("stop() permanently halts autoplay and does not resume", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.advanceClock(5000);
    expect(h.advance).toHaveBeenCalledTimes(1);
    h.autoplay.stop();
    h.advanceClock(60_000);
    expect(h.advance).toHaveBeenCalledTimes(1);
    // A late start() after a manual stop is ignored.
    h.autoplay.start();
    h.advanceClock(60_000);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("stop() mid-dwell cancels the pending advance", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.advanceClock(3000);
    h.autoplay.stop();
    h.advanceClock(5000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("pauses on hover and resumes on un-hover (resumable)", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.advanceClock(3000);
    h.autoplay.setHovered(true);
    h.advanceClock(60_000);
    expect(h.advance).not.toHaveBeenCalled();
    h.autoplay.setHovered(false);
    // Timer restarts fresh on resume.
    h.advanceClock(4999);
    expect(h.advance).not.toHaveBeenCalled();
    h.advanceClock(1);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("pauses while the tab is hidden and resumes when visible", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.autoplay.setHidden(true);
    h.advanceClock(60_000);
    expect(h.advance).not.toHaveBeenCalled();
    h.autoplay.setHidden(false);
    h.advanceClock(5000);
    expect(h.advance).toHaveBeenCalledTimes(1);
  });

  it("stop() wins over a concurrent hover-resume", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.autoplay.setHovered(true);
    h.autoplay.stop();
    h.autoplay.setHovered(false);
    h.advanceClock(60_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("dispose() halts the timer", () => {
    const h = makeHarness(5000);
    h.autoplay.start();
    h.advanceClock(3000);
    h.autoplay.dispose();
    h.advanceClock(60_000);
    expect(h.advance).not.toHaveBeenCalled();
  });

  it("reports running-edge transitions", () => {
    const onRunningChange = vi.fn();
    const autoplay = new GalleryAutoplay({
      intervalMs: 5000,
      advance: vi.fn(),
      onRunningChange,
    });
    autoplay.start();
    expect(onRunningChange).toHaveBeenLastCalledWith(true);
    autoplay.setHovered(true);
    expect(onRunningChange).toHaveBeenLastCalledWith(false);
    autoplay.setHovered(false);
    expect(onRunningChange).toHaveBeenLastCalledWith(true);
    autoplay.stop();
    expect(onRunningChange).toHaveBeenLastCalledWith(false);
  });
});
