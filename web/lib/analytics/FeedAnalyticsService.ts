// PostHog beacons for the web persona feed.
//
// The `/feed/event` endpoint is a separate channel for popup and conversion
// beacons. Swipe, dwell, retry, and error events land in PostHog so
// dashboards aggregate on the same event names.
//
// `posthog-js` is loaded by the host page (script tag or future
// dependency) and installs `window.posthog`. We dispatch through that
// global so this module does not require a build-time import, which keeps
// the feed working in environments where PostHog isn't configured (the
// `capture` becomes a no-op).

export type FeedAnalyticsEvent =
  | {
      kind: "feed_swipe";
      direction: "forward" | "back";
      /** Pixel distance of the swipe gesture, when available. */
      distancePx?: number;
      fromPersonaId?: string | null;
      toPersonaId?: string | null;
    }
  | {
      kind: "feed_dwell";
      personaId: string;
      dwellMs: number;
    }
  | {
      kind: "feed_retry";
      personaId?: string | null;
    }
  | {
      kind: "feed_error";
      message: string;
      personaId?: string | null;
    };

export interface FeedAnalyticsReporter {
  capture(event: FeedAnalyticsEvent): void;
}

interface PostHogLike {
  capture(name: string, properties?: Record<string, unknown>): void;
}

function resolvePostHog(): PostHogLike | null {
  if (typeof window === "undefined") return null;
  const ph = (window as unknown as { posthog?: PostHogLike }).posthog;
  return ph && typeof ph.capture === "function" ? ph : null;
}

function toProps(event: FeedAnalyticsEvent): Record<string, unknown> {
  switch (event.kind) {
    case "feed_swipe":
      return {
        direction: event.direction,
        distance_px: event.distancePx,
        from_persona_id: event.fromPersonaId ?? null,
        to_persona_id: event.toPersonaId ?? null,
      };
    case "feed_dwell":
      return {
        persona_id: event.personaId,
        dwell_ms: event.dwellMs,
      };
    case "feed_retry":
      return { persona_id: event.personaId ?? null };
    case "feed_error":
      return {
        message: event.message,
        persona_id: event.personaId ?? null,
      };
  }
}

export class FeedAnalyticsService implements FeedAnalyticsReporter {
  private readonly resolver: () => PostHogLike | null;

  constructor(opts: { resolver?: () => PostHogLike | null } = {}) {
    this.resolver = opts.resolver ?? resolvePostHog;
  }

  capture(event: FeedAnalyticsEvent): void {
    const ph = this.resolver();
    if (!ph) return;
    try {
      ph.capture(event.kind, toProps(event));
    } catch {
      // Analytics must never demote the experience.
    }
  }
}

/** Singleton used by the feed UI when no DI override is supplied. */
export const feedAnalytics: FeedAnalyticsReporter = new FeedAnalyticsService();
