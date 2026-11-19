"use client";

// Consumer-feed global chat overlay.
//
// Renders the trailing 5 messages of a simulated viewer-comment stream
// (per active product), plus a "Say something…" composer that injects
// the user's typed message into the same stream so they see their own
// comment land in the rolling window. No DB persistence, no API call.
//
// Visual design follows live-shopping chat conventions (Whatnot,
// TikTok Live): bottom-left stack, last-N rolling list, lightweight
// avatars + body, brand-accent highlight for high-energy fragments.
// The container masks the top edge so older messages soft-fade instead
// of popping out — paired with a slide-up entry animation on each new
// row, the stream reads as smooth even at peak (sub-second) cadence.
//
// Architecturally this component is *just a view*. All scheduling +
// state lives in `GlobalChatStore` + `SimulatedChatSource`, surfaced
// here through the `useGlobalChat` hook. Swapping the simulator for a
// real wire transport is a one-line change inside the hook.

import { useCallback, useState, type FormEvent } from "react";
import { useGlobalChat, type GlobalChatBridge } from "@/lib/chat/useGlobalChat";
import type { GlobalChatMessage } from "@/lib/chat/globalChat";
import { HIGHLIGHT_FRAGMENTS } from "@/lib/chat/CommentCatalog";

interface GlobalChatOverlayProps {
  /** Active product driving the script. `null` when no product is
   *  pinned — the overlay collapses to just the composer + empty stack
   *  so the user can still send a message between pitches. */
  productId: string | null;
  /** Optional override for the self handle (e.g. signed-in username).
   *  Falls back to `"anon"`. */
  selfHandle?: string;
  /** Live cross-client transport. When provided, every user message
   *  fans out to other viewers on the same channel via LiveKit text
   *  streams, and inbound messages merge into the rolling buffer. */
  chatBridge?: GlobalChatBridge | null;
  /** Visual density.
   *
   *   - `"full"` (default): five-message stack + standard composer.
   *     The chat IS the social surface in avatar mode and gets the
   *     full footprint it deserves.
   *
   *   - `"compact"`: three-message stack, smaller rows, slimmer
   *     composer. Used in product-focus mode where the product is the
   *     hero and the chat should recede to an *ambient peek* — present
   *     enough to feel "live" without competing with the gallery for
   *     attention. Matches the receding-chat pattern Whatnot / TikTok
   *     Live use when their product-detail sheet pops over the video.
   *
   *   Density is a render-time prop only — the underlying store, the
   *   rolling buffer, and the simulator all live inside `useGlobalChat`
   *   and survive a density swap with no remount, so the persistent-
   *   stream guarantee from FeedPager's lift-out-of-conditional fix
   *   carries through. */
  density?: "full" | "compact";
  /** Forwarded for layout — caller may want to offset the overlay
   *  above the pinned product card on certain breakpoints. */
  className?: string;
}

// Both density modes show the same trailing slice — the "live crowd"
// feeling depends on enough rows to read as movement, and three rows
// was too few to register as a chat at all. Density now only affects
// the *visual size* of each row + the composer, not the count.
const VISIBLE_COUNT = 5;
const MAX_COMPOSER_LENGTH = 200;

export function GlobalChatOverlay({
  productId,
  selfHandle,
  chatBridge,
  density = "full",
  className,
}: GlobalChatOverlayProps) {
  const { messages, send } = useGlobalChat({ productId, selfHandle, chatBridge });
  const [draft, setDraft] = useState("");

  const isCompact = density === "compact";
  const visible = messages.slice(-VISIBLE_COUNT);

  const handleSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const text = draft.trim();
      if (!text) return;
      send(text);
      setDraft("");
    },
    [draft, send],
  );

  return (
    <div
      className={[
        // Mobile-first sizing. On a 360-pt portrait viewport the overlay
        // takes ~75% of the column (270 pt) — leaves room for the action
        // rail on the right and the seller PiP without overlap. On
        // tablets and desktop the column is letterboxed to a phone
        // frame, so we cap at 20rem (320 pt) — wider would crowd the
        // pinned product card on the same edge.
        //
        // Compact mode trims the desktop cap and the mobile width by
        // ~20% so the chat sits comfortably alongside a product gallery
        // without crowding the pagination dots / action card.
        isCompact
          ? "pointer-events-none flex w-[62%] max-w-[16rem] flex-col gap-1 sm:w-[16rem]"
          : "pointer-events-none flex w-[78%] max-w-[20rem] flex-col gap-1.5 sm:w-[20rem]",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      data-testid="GlobalChatOverlay"
      data-density={density}
      aria-label="Live chat"
    >
      <div
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        className="chat-stack-fade flex flex-col justify-end gap-1"
        // Compact stack is ~58% as tall as the full stack — three rows
        // of smaller copy instead of five. The mask gradient at the top
        // still fades the oldest row out so the swap from compact ↔
        // full reads as a graceful settle, not a hard cut.
        style={{ minHeight: isCompact ? "5rem" : "8.5rem" }}
      >
        {visible.map((message) => (
          <ChatRow key={message.id} message={message} compact={isCompact} />
        ))}
      </div>

      <form onSubmit={handleSubmit} className="pointer-events-auto" aria-label="Send a comment">
        <label htmlFor="global-chat-input" className="sr-only">
          Say something
        </label>
        {/* Composer sized for thumb-reach: min-h 44px hits the
            WCAG 2.5.5 touch-target floor, and the 16px text size keeps
            iOS Safari from zooming on focus. Glass-morphism backdrop
            (semi-transparent + blur) gives legibility over the video
            without a hard letterbox bar.

            Compact composer drops to min-h 38px (still well above the
            visual-comfort floor) and shrinks the text to 14px on
            mobile so it doesn't dominate the product card area. iOS
            zoom-on-focus is acceptable here because product mode is
            already a deliberate "I'm looking at the product" state. */}
        <input
          id="global-chat-input"
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value.slice(0, MAX_COMPOSER_LENGTH))}
          placeholder="Say something…"
          maxLength={MAX_COMPOSER_LENGTH}
          autoComplete="off"
          autoCorrect="on"
          spellCheck
          enterKeyHint="send"
          inputMode="text"
          className={
            isCompact
              ? "min-h-[38px] w-full rounded-full border border-white/15 bg-black/45 px-3.5 py-1.5 text-sm text-white placeholder:text-white/55 backdrop-blur-md focus:border-white/30 focus:outline-none focus:ring-2 focus:ring-white/20 sm:py-2 sm:text-xs"
              : "min-h-[44px] w-full rounded-full border border-white/15 bg-black/55 px-4 py-2 text-base text-white placeholder:text-white/55 backdrop-blur-md focus:border-white/30 focus:outline-none focus:ring-2 focus:ring-white/20 sm:py-2.5 sm:text-sm"
          }
        />
      </form>
    </div>
  );
}

function ChatRow({ message, compact }: { message: GlobalChatMessage; compact: boolean }) {
  const isSelf = message.authorKind === "self";
  return (
    <div
      // The wrapper is keyed by message id at the parent, so each new row
      // mounts fresh and replays the keyframe. No exit animation — the
      // mask gradient at the top of the stack provides the visual fade
      // when this row is eventually unmounted.
      className={
        compact
          ? "chat-row-in pointer-events-none flex items-start gap-1.5 text-[11px] leading-snug text-white/95 drop-shadow-[0_1px_2px_rgba(0,0,0,0.85)]"
          : "chat-row-in pointer-events-none flex items-start gap-2 text-[13px] leading-snug text-white drop-shadow-[0_1px_2px_rgba(0,0,0,0.85)]"
      }
      data-message-id={message.id}
      data-author-kind={message.authorKind}
    >
      <AvatarBubble handle={message.authorHandle} isSelf={isSelf} compact={compact} />
      {/* Semi-transparent black plate sits behind the handle + body so
          white text stays readable over busy background video. Width is
          shrink-wrapped (`w-fit`) rather than column-spanning so the
          plate hugs short messages instead of stretching to a full row
          of empty black. */}
      <div
        className={
          compact
            ? "w-fit min-w-0 max-w-full rounded-xl bg-black/55 px-2 py-0.5 backdrop-blur-sm"
            : "w-fit min-w-0 max-w-full rounded-xl bg-black/55 px-2.5 py-1 backdrop-blur-sm"
        }
      >
        <div className="flex items-center gap-1.5">
          <span
            className={
              isSelf
                ? compact
                  ? "text-[10px] font-semibold text-brand-accent"
                  : "text-[12px] font-semibold text-brand-accent"
                : compact
                  ? "text-[10px] font-semibold text-white/85"
                  : "text-[12px] font-semibold text-white/90"
            }
          >
            {message.authorHandle}
          </span>
          {isSelf && !compact && (
            <span className="rounded-full bg-brand-accent/80 px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide text-white">
              you
            </span>
          )}
        </div>
        <p className="break-words text-white">
          <HighlightedText text={message.text} />
        </p>
      </div>
    </div>
  );
}

function AvatarBubble({
  handle,
  isSelf,
  compact,
}: {
  handle: string;
  isSelf: boolean;
  compact: boolean;
}) {
  // Deterministic colour pick from a fixed palette — the same handle
  // always gets the same chip colour, so a returning viewer's avatar
  // looks consistent across pitches.
  const palette = [
    "bg-rose-500",
    "bg-amber-500",
    "bg-emerald-500",
    "bg-sky-500",
    "bg-violet-500",
    "bg-fuchsia-500",
    "bg-orange-500",
    "bg-teal-500",
  ];
  const hash = Array.from(handle).reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  const colour = isSelf ? "bg-brand-accent" : palette[hash % palette.length];
  const initial =
    handle
      .replace(/[^a-zA-Z]/g, "")
      .charAt(0)
      .toUpperCase() || "?";
  return (
    <div
      className={
        compact
          ? `flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full text-[9px] font-bold text-white ${colour}`
          : `flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white ${colour}`
      }
      aria-hidden
    >
      {initial}
    </div>
  );
}

/**
 * Highlight a curated set of high-energy fragments in brand-accent
 * orange — mirrors the inline coloured highlight Whatnot uses to draw
 * the eye to peak-moment chat. Pure render-time string scan; the catalog
 * is small enough that the O(n × m) check is fine.
 */
function HighlightedText({ text }: { text: string }) {
  // Find the first fragment that matches, painted in the brand accent.
  for (const fragment of HIGHLIGHT_FRAGMENTS) {
    const idx = text.indexOf(fragment);
    if (idx === -1) continue;
    const before = text.slice(0, idx);
    const after = text.slice(idx + fragment.length);
    return (
      <>
        {before}
        <span className="font-semibold text-brand-accent">{fragment}</span>
        {after}
      </>
    );
  }
  return <>{text}</>;
}
