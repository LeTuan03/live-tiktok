"use client";

import { usePathname } from "next/navigation";

import { SoShopLogoButton } from "./SoShopLogoButton";
import { HIDE_VIDEO_UI } from "@/lib/feed/flags";

// Floating top chrome over the full-bleed consumer feed.
//
// Just the SoShop wordmark (left). The bar floats over content under a
// soft top-down scrim — no opaque bar, no hamburger overflow, no
// in-header unmute pill. Audio control lives on the right rail of the
// active feed card; menu affordances are deferred until we have real
// navigation surfaces.
//
// Layout follows the consensus pattern across TikTok / Whatnot / Reels:
// 52pt content height above the safe-area inset, white knockout
// wordmark with a subtle shadow so it survives a blown-out frame.
// `/studio`, `/watch` and `/onair` (the studio and its viewer pages) draw
// their own headers.
const HIDDEN_PATH_PREFIXES = ["/persona", "/customize", "/studio", "/watch", "/onair"];

export function ConsumerTopChrome() {
  const pathname = usePathname() ?? "/";

  if (HIDE_VIDEO_UI) {
    return null;
  }

  if (HIDDEN_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return null;
  }

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50">
      <div
        aria-hidden
        className="absolute inset-x-0 top-0 h-24 bg-gradient-to-b from-black/45 via-black/15 to-transparent"
      />
      <div
        className="pointer-events-auto relative flex items-center justify-between px-4 pb-2"
        style={{ paddingTop: "max(0.5rem, env(safe-area-inset-top))" }}
      >
        <SoShopLogoButton />
      </div>
    </header>
  );
}
