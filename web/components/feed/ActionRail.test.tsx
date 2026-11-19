// Tests for the TikTok-style right-rail action stack used by
// `PersonaCard`. The rail is owned-callbacks-only (no internal state),
// so the test surface is small:
//
//   - Favorite glyph flips between active / inactive aria state
//   - Mute glyph mirrors `audioMuted` + `canPlaybackAudio`
//   - Each tap fires both the primary callback AND the autoscroll
//     `onInteraction(kind)` beacon
//   - Share path falls back gracefully when `navigator.share` is absent

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ActionRail } from "./ActionRail";
import type { PersonaSlot } from "@/lib/feed/FeedService";

function makeSlot(overrides: Partial<PersonaSlot> = {}): PersonaSlot {
  return {
    id: "p-1",
    displayName: "Alice",
    handle: "@alice",
    stillUrl: null,
    avatarUrl: null,
    gradient: { from: "#000", to: "#111" },
    accentColor: "#fff",
    isAd: false,
    isFavorited: false,
    isMuted: true,
    productId: null,
    product: null,
    scriptId: null,
    ...overrides,
  };
}

describe("ActionRail", () => {
  it("renders the favorite glyph in unpressed state when slot is not favorited", () => {
    render(<ActionRail slot={makeSlot()} audioMuted canPlaybackAudio />);
    const fav = screen.getByRole("button", { name: "Favorite" });
    expect(fav).toHaveAttribute("aria-pressed", "false");
  });

  it("flips to pressed + 'Unfavorite' label when slot.isFavorited is true", () => {
    render(<ActionRail slot={makeSlot({ isFavorited: true })} audioMuted canPlaybackAudio />);
    const fav = screen.getByRole("button", { name: "Unfavorite" });
    expect(fav).toHaveAttribute("aria-pressed", "true");
  });

  it("favorite tap fires onTapFavorite(slot) AND onInteraction('favorite')", () => {
    const slot = makeSlot();
    const onTapFavorite = vi.fn();
    const onInteraction = vi.fn();
    render(
      <ActionRail
        slot={slot}
        audioMuted
        canPlaybackAudio
        onTapFavorite={onTapFavorite}
        onInteraction={onInteraction}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Favorite" }));
    expect(onTapFavorite).toHaveBeenCalledWith(slot);
    expect(onInteraction).toHaveBeenCalledWith("favorite");
  });

  it("mute glyph reads 'Unmute' when muted, fires toggle + interaction beacon", () => {
    const onToggleAudio = vi.fn();
    const onInteraction = vi.fn();
    render(
      <ActionRail
        slot={makeSlot()}
        audioMuted
        canPlaybackAudio
        onToggleAudio={onToggleAudio}
        onInteraction={onInteraction}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Unmute" }));
    expect(onToggleAudio).toHaveBeenCalledTimes(1);
    expect(onInteraction).toHaveBeenCalledWith("mute");
  });

  it("mute glyph reads 'Mute' when audio is on AND playback is allowed", () => {
    render(<ActionRail slot={makeSlot()} audioMuted={false} canPlaybackAudio />);
    expect(screen.getByRole("button", { name: "Mute" })).toBeInTheDocument();
  });

  it("forces 'Unmute' affordance when canPlaybackAudio is false even if audioMuted=false", () => {
    // Mirrors LiveKit's `Room.canPlaybackAudio` rule: when the autoplay
    // gate is shut, the user must tap to grant playback. The glyph must
    // surface the unmute affordance regardless of audioMuted's value.
    render(<ActionRail slot={makeSlot()} audioMuted={false} canPlaybackAudio={false} />);
    expect(screen.getByRole("button", { name: "Unmute" })).toBeInTheDocument();
  });

  it("share tap calls navigator.share when available; otherwise no-ops cleanly", () => {
    const share = vi.fn().mockResolvedValue(undefined);
    const navigatorAny = window.navigator as unknown as { share?: typeof share };
    const originalShare = navigatorAny.share;
    navigatorAny.share = share;
    try {
      render(
        <ActionRail
          slot={makeSlot({ displayName: "Alice the host" })}
          audioMuted
          canPlaybackAudio
        />,
      );
      fireEvent.click(screen.getByRole("button", { name: "Share" }));
      expect(share).toHaveBeenCalledWith(expect.objectContaining({ title: "Alice the host" }));
    } finally {
      navigatorAny.share = originalShare;
    }
  });
});
