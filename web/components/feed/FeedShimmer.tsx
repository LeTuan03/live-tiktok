"use client";
// Sweeping-gradient shimmer card. A dark base with a horizontal gradient
// sweep and idle-when-hidden behavior. Used as the cold-start state of a
// `PersonaCard` and as the renderer mask while a swap is in flight.

interface FeedShimmerProps {
  /** Optional overlay class — the shimmer fills its parent. */
  className?: string;
}

export function FeedShimmer({ className }: FeedShimmerProps) {
  return (
    <div
      aria-hidden
      className={`relative h-full w-full overflow-hidden bg-[#141418] ${className ?? ""}`}
    >
      <div className="feed-shimmer absolute inset-0" />
      <style jsx>{`
        .feed-shimmer {
          background: linear-gradient(
            100deg,
            rgba(255, 255, 255, 0.04) 0%,
            rgba(255, 255, 255, 0.16) 50%,
            rgba(255, 255, 255, 0.04) 100%
          );
          background-size: 250% 100%;
          animation: feed-shimmer 1.4s linear infinite;
          will-change: background-position;
        }
        @keyframes feed-shimmer {
          0% {
            background-position: 200% 0;
          }
          100% {
            background-position: -100% 0;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .feed-shimmer {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
