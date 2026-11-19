"use client";

// Right-rail action stack for `PersonaCard`. TikTok-style: a vertical
// column of 44pt tap targets each wrapping a ~26pt glyph, drawn in
// white with a soft drop shadow over the bottom-scrim gradient. The
// favorite glyph is gated through `<GatedAction>` so signed-out
// the toggle commits directly (the app is open access).
//
// Owns no state of its own — every interactive action is a callback
// the parent provides. `audioMuted` + `canPlaybackAudio` flow in from
// the shared mute state on `FeedPager` so the rail glyph stays in
// lockstep with the tap-the-video gesture inside `LiveRoomPlayer`.

import type { ReactNode } from "react";
import { Bookmark, Heart, MessageCircle, Send, Volume2, VolumeX } from "lucide-react";
import { GatedAction } from "@/components/auth/GatedAction";
import type { PersonaSlot } from "@/lib/feed/FeedService";
import type { InteractionKind } from "@/lib/feed/AutoscrollPolicy";

export interface ActionRailProps {
  slot: PersonaSlot;
  audioMuted: boolean;
  canPlaybackAudio: boolean;
  onToggleAudio?: () => void;
  onTapFavorite?: (slot: PersonaSlot) => void;
  onInteraction?: (kind: InteractionKind) => void;
}

export function ActionRail({
  slot,
  audioMuted,
  canPlaybackAudio,
  onToggleAudio,
  onTapFavorite,
  onInteraction,
}: ActionRailProps) {
  return (
    <div
      className="absolute right-2 z-20 flex flex-col items-center gap-2"
      style={{
        bottom: "calc(env(safe-area-inset-bottom, 0px) + 4.5rem)",
      }}
    >
      <GatedAction
        onAction={() => {
          onInteraction?.("favorite");
          onTapFavorite?.(slot);
        }}
      >
        <RailButton
          label={slot.isFavorited ? "Unfavorite" : "Favorite"}
          active={slot.isFavorited}
          activeColor="text-rose-500"
        >
          <Heart size={26} strokeWidth={2} fill={slot.isFavorited ? "currentColor" : "none"} />
        </RailButton>
      </GatedAction>
      <RailButton label="Comment">
        <MessageCircle size={26} strokeWidth={2} />
      </RailButton>
      <RailButton
        label="Share"
        onClick={() => {
          const url = typeof window !== "undefined" ? window.location.href : "";
          if (typeof navigator !== "undefined" && navigator.share) {
            void navigator.share({ title: slot.displayName, url }).catch(() => {});
          }
        }}
      >
        <Send size={24} strokeWidth={2} />
      </RailButton>
      <RailButton label="Save">
        <Bookmark size={26} strokeWidth={2} />
      </RailButton>
      <RailButton
        label={audioMuted || !canPlaybackAudio ? "Unmute" : "Mute"}
        onClick={() => {
          onInteraction?.("mute");
          onToggleAudio?.();
        }}
      >
        {audioMuted || !canPlaybackAudio ? (
          <VolumeX size={26} strokeWidth={2} />
        ) : (
          <Volume2 size={26} strokeWidth={2} />
        )}
      </RailButton>
    </div>
  );
}

interface RailButtonProps {
  label: string;
  onClick?: () => void;
  active?: boolean;
  activeColor?: string;
  children: ReactNode;
}

/**
 * Right-rail action button. Standard TikTok/Reels treatment: a 44pt
 * tap target wrapping a ~26pt glyph, white with a soft drop shadow,
 * optional active tint when the state toggles on.
 */
function RailButton({
  label,
  onClick,
  active = false,
  activeColor = "text-white",
  children,
}: RailButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={`flex h-11 w-11 items-center justify-center text-white drop-shadow-[0_2px_4px_rgba(0,0,0,0.6)] transition active:scale-90 ${
        active ? activeColor : ""
      }`}
    >
      {children}
    </button>
  );
}
