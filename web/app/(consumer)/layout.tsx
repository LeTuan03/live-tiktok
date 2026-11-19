import type { Metadata, Viewport } from "next";
import { ConsumerTopChrome } from "@/components/consumer/BottomTabs";

export const metadata: Metadata = {
  title: "SoShop",
  description: "SoShop, a demo of AI-hosted live shopping built on LemonSlice",
};

// Mobile-web shell: status-bar-respecting top inset, edge-to-edge layout,
// no pinch-zoom (the feed and live rooms compose their own zoom semantics).
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
};

// `modal` is the `@modal` parallel slot that renders
// modal-cover overlays (Live, AskAvatar) on top of the underlying
// route. Slot defaults to `null` via `@modal/default.tsx`, so non-
// modal navigations are unaffected.
//
// `<ConsumerTopChrome>` is the global menu surface — it self-hides on
// full-bleed routes (Live, AskAvatar) so the feed/live experience stays
// edge-to-edge.

// Force dynamic across the whole consumer route group: every page reads
// runtime state (LiveKit tokens, channel list) on each request, so a
// prerender pass would only capture a stale snapshot. Production deploys
// still stream HTML cheaply because the surfaces are anonymous-by-default
// and the underlying API responses are short-lived.
export const dynamic = "force-dynamic";
export default function ConsumerLayout({
  children,
  modal,
}: {
  children: React.ReactNode;
  modal: React.ReactNode;
}) {
  return (
    <div className="theme-consumer min-h-[100dvh] bg-[var(--surface-bg)] text-[var(--surface-fg)]">
      <ConsumerTopChrome />
      <main className="w-full">{children}</main>
      {modal}
    </div>
  );
}
