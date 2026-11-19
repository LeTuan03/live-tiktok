/**
 * Channel-ring feed scroll-cycle smoke.
 *
 * Validates the channel-ring contract end-to-end against a real backend:
 *
 *   1. `/` loads → `ChannelRoomCoordinator.init()` connects N rooms in
 *      parallel with `autoSubscribe: false`.
 *   2. The center cell's `<video>` element receives an attached
 *      `MediaStream` within 2s (live track binds).
 *   3. Scrolling by `window.innerHeight` flips the active channel —
 *      `swipeNext()` toggles publication subscription on the previous
 *      and next rooms — and the video element rebinds to a **different**
 *      track id.
 *   4. **No `Room.connect()` is invoked after the initial bootstrap**.
 *      This is the regression we care about: a naive implementation
 *      would reconnect when the user swipes; the coordinator must reuse
 *      the long-lived rooms and only flip subscription.
 *
 * Hermeticity — the test never spins up its own backend or LiveKit
 * server. It points at `STAGING_URL` (a deployed env with seeded
 * channels) so the local dev loop is unaffected. When `STAGING_URL` is
 * unset, the spec is skipped rather than executed against an empty dev
 * server (the contract requires real video subscribing).
 *
 * Feature flag — `feed_channels_v1` ships behind PostHog. Tests
 * should not be gated on a live PostHog rollout, so a synthetic
 * `feed_channels_v1=true` cookie is set in `beforeEach`; the web app
 * trusts that cookie as a PostHog flag override in non-production
 * environments.
 *
 * Instrumentation — `window.__lkConnects` counts every
 * `LiveKitClient.Room.connect()` invocation. We snapshot the count
 * after bootstrap and again after the scroll and assert they are equal.
 * Patching `Room.connect` (rather than counting WebSockets) keeps the
 * assertion at the same API surface the unit suite already covers, so
 * a coordinator change that adds an extra `connect()` call surfaces in
 * both layers.
 */
import { test, expect, type Page } from "@playwright/test";

const STAGING_URL = process.env.STAGING_URL?.replace(/\/+$/, "") ?? "";

// Skip the entire describe block if no staging URL is configured. The
// spec is hermetic-by-design: pointing it at `npm run dev` would
// exercise an empty `/channels` response and tell us nothing about the
// scroll cycle.
test.describe(
  STAGING_URL
    ? "feed-channels scroll cycle"
    : "feed-channels scroll cycle (skipped — STAGING_URL unset)",
  () => {
    test.skip(
      !STAGING_URL,
      "Set STAGING_URL to run against a staging environment with seeded channels.",
    );

    // Strip out the dev-server `webServer` and `baseURL` so this spec
    // doesn't accidentally boot a Next.js server when run in isolation.
    test.use({ baseURL: STAGING_URL || undefined });

    test.beforeEach(async ({ context }) => {
      if (!STAGING_URL) return;
      const url = new URL(STAGING_URL);
      // PostHog flag override cookie — the consumer page reads this as a
      // local opt-in to bypass the live experiment grant. Setting it on
      // both the bare host and the URL ensures the cookie sticks across
      // staging subdomains.
      await context.addCookies([
        {
          name: "feed_channels_v1",
          value: "true",
          domain: url.hostname,
          path: "/",
          sameSite: "Lax",
        },
      ]);

      // Patch `Room.connect` on every page load — runs before any app
      // script in the document. `window.__lkConnects` is the shared
      // counter the test reads after each phase.
      await context.addInitScript(() => {
        const w = window as unknown as {
          __lkConnects: number;
          __lkPatched: boolean;
        };
        w.__lkConnects = 0;
        w.__lkPatched = false;

        const tryPatch = (): boolean => {
          // livekit-client exposes `Room` either on `window.LivekitClient`
          // (UMD) or via dynamic import — App Router's bundle puts it on
          // the global only after the entry chunk evaluates. We poll
          // until the constructor shows up.
          const lk = (
            window as unknown as {
              LivekitClient?: { Room?: { prototype: { connect: unknown } } };
            }
          ).LivekitClient;
          const Room = lk?.Room;
          if (!Room || typeof (Room.prototype as { connect: unknown }).connect !== "function") {
            return false;
          }
          const proto = Room.prototype as {
            connect: (...args: unknown[]) => Promise<void>;
          };
          const original = proto.connect;
          proto.connect = function patchedConnect(this: unknown, ...args: unknown[]) {
            w.__lkConnects += 1;
            return original.apply(this, args);
          };
          w.__lkPatched = true;
          return true;
        };

        // Fast path — global already present (rare on cold load).
        if (!tryPatch()) {
          // The chunk likely lands after a `requestAnimationFrame`-ish
          // boundary. Poll for up to 5s; the page-load wait below will
          // observe `__lkPatched` and fail loudly if the SDK never
          // surfaces (so a future bundle change can't silently bypass
          // the connect-count assertion).
          const start = Date.now();
          const timer = window.setInterval(() => {
            if (tryPatch() || Date.now() - start > 5_000) {
              window.clearInterval(timer);
            }
          }, 50);
        }
      });
    });

    test("scroll rebinds a different track without reconnecting any room", async ({ page }) => {
      await page.goto("/");

      // The feed pager is the consumer-route landmark — wait on the
      // accessible role so the test isn't coupled to the CSS class.
      const pager = page.getByRole("feed", { name: /live channel feed/i });
      await pager.waitFor({ state: "visible", timeout: 15_000 });

      // The center cell is the active LiveKit subscription. Its <video>
      // gets `srcObject` set by `RemoteVideoTrack.attach()` once the SFU
      // delivers the first packet. The acceptance criterion gives us 2s
      // for the initial bind from page-visible.
      const video = pager.locator('div[data-cell-ord="1"] video').first();
      await video.waitFor({ state: "attached", timeout: 2_000 });

      const initialTrackId = await waitForTrackId(page, video, /* timeoutMs */ 2_000);
      expect(initialTrackId, "expected a MediaStream track within 2s of /").toBeTruthy();

      // Confirm the patch actually landed; otherwise the connect-count
      // assertion below is a false negative.
      const patched = await page.evaluate(
        () => (window as unknown as { __lkPatched: boolean }).__lkPatched,
      );
      expect(patched, "expected livekit-client Room.connect to be patched").toBe(true);

      // Snapshot the bootstrap connect count. The coordinator opens one
      // connect() per channel in parallel during `init()`; we assert
      // strict equality against this number after the scroll.
      const bootstrapConnects = await page.evaluate(
        () => (window as unknown as { __lkConnects: number }).__lkConnects,
      );
      expect(bootstrapConnects, "expected at least one Room.connect() during init").toBeGreaterThan(
        0,
      );

      // Programmatic scroll by viewport height — the IntersectionObserver
      // in `FeedPager` interprets this as a "next" gesture and calls
      // `coordinator.swipeNext()`.
      await pager.evaluate((el) => {
        (el as HTMLElement).scrollBy({ top: window.innerHeight, behavior: "auto" });
      });

      // The pager recenter happens in `useLayoutEffect`, and the
      // coordinator emits `active-track-changed` synchronously when the
      // new room already has a video publication. Allow up to 5s for the
      // rebind in case the SFU is still warming the new track.
      const nextTrackId = await waitForTrackIdChange(page, video, initialTrackId, 5_000);
      expect(nextTrackId, "expected a different track id after one viewport scroll").not.toBe(
        initialTrackId,
      );
      expect(nextTrackId).toBeTruthy();

      // **The contract under test.** No additional Room.connect() should
      // have been invoked after the bootstrap — the coordinator must
      // reuse the long-lived rooms and only flip subscription.
      const afterScrollConnects = await page.evaluate(
        () => (window as unknown as { __lkConnects: number }).__lkConnects,
      );
      expect(
        afterScrollConnects,
        `expected no new Room.connect() calls after the initial bootstrap (was ${bootstrapConnects}, now ${afterScrollConnects})`,
      ).toBe(bootstrapConnects);
    });
  },
);

/**
 * Poll for the video element's first MediaStream track id, returning
 * the id (or empty string) once it's stable. The `attach()` call sets
 * `srcObject` to a `MediaStream`; `MediaStream.getVideoTracks()[0].id`
 * is unique per remote track, so we use it as the rebind discriminator.
 */
async function waitForTrackId(
  page: Page,
  video: ReturnType<Page["locator"]>,
  timeoutMs: number,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const id = await readTrackId(video);
    if (id) return id;
    await page.waitForTimeout(50);
  }
  return "";
}

async function waitForTrackIdChange(
  page: Page,
  video: ReturnType<Page["locator"]>,
  previous: string,
  timeoutMs: number,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const id = await readTrackId(video);
    if (id && id !== previous) return id;
    await page.waitForTimeout(50);
  }
  return await readTrackId(video);
}

async function readTrackId(video: ReturnType<Page["locator"]>): Promise<string> {
  return await video.evaluate((el) => {
    const v = el as HTMLVideoElement;
    const stream = v.srcObject as MediaStream | null;
    if (!stream) return "";
    const tracks = stream.getVideoTracks();
    return tracks.length > 0 ? tracks[0].id : "";
  });
}
