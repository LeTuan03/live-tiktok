// Wire-shape regression: `GET /channels/{id}/current` returns the
// envelope `{ ad: AdSnapshot | null }` (server: `CurrentAdResponse`).
// The previous hand-mirror typed a flat `{ ad_id, product, ... }`
// shape, so `adaptCurrentAd` silently produced `null` for product /
// persona on every call. These tests pin the envelope so a future
// re-divergence is caught at unit-test time, not in dev.

import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { getCurrentAd } from "./channels";

const ORIGINAL_BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
});

afterEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE;
  vi.restoreAllMocks();
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function spyFetch(response: Response): MockInstance {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

describe("getCurrentAd — envelope projection", () => {
  it("projects the populated envelope into the camelCase domain shape", async () => {
    spyFetch(
      jsonResponse(200, {
        ad: {
          id: "ad-1",
          product: {
            id: "prod-1",
            title: "Aurora Tea",
            price_cents: 1290,
            currency: "USD",
            image_url: "https://img.example/tea.png",
            deep_link_url: "soshop://product/tea",
            landing_url: "https://shop.example/tea",
          },
          persona: {
            id: "persona-1",
            display_name: "Leila",
            avatar_url: "https://img.example/leila.png",
          },
          advertiser: { id: "adv-1", name: "Aurora Goods" },
          deep_link_url: "soshop://product/tea",
          started_at: "2024-01-01T00:00:00Z",
          duration_ms: 15_000,
        },
      }),
    );

    const ad = await getCurrentAd("channel-0");
    expect(ad).not.toBeNull();
    expect(ad?.adId).toBe("ad-1");
    expect(ad?.product?.id).toBe("prod-1");
    expect(ad?.product?.title).toBe("Aurora Tea");
    expect(ad?.product?.priceCents).toBe(1290);
    expect(ad?.product?.currency).toBe("USD");
    expect(ad?.product?.imageUrl).toBe("https://img.example/tea.png");
    expect(ad?.product?.deepLinkUrl).toBe("soshop://product/tea");
    expect(ad?.product?.landingUrl).toBe("https://shop.example/tea");
    expect(ad?.persona?.displayName).toBe("Leila");
    expect(ad?.persona?.avatarUrl).toBe("https://img.example/leila.png");
    expect(ad?.advertiser?.name).toBe("Aurora Goods");
    expect(ad?.startedAt).toBe("2024-01-01T00:00:00Z");
    expect(ad?.durationMs).toBe(15_000);
  });

  it("handles the empty-envelope (`{ ad: null }`) case without throwing", async () => {
    spyFetch(jsonResponse(200, { ad: null }));

    const ad = await getCurrentAd("channel-0");
    expect(ad).not.toBeNull();
    expect(ad?.adId).toBeNull();
    expect(ad?.product).toBeNull();
    expect(ad?.persona).toBeNull();
    expect(ad?.advertiser).toBeNull();
    expect(ad?.startedAt).toBeNull();
    expect(ad?.durationMs).toBeNull();
  });

  it("returns null on a 204 No Content response", async () => {
    // The server currently always returns 200 (with `ad: null` for
    // the no-ad state), but the client tolerates 204 for forward
    // compatibility — covers the dead branch without breaking it.
    spyFetch(new Response(null, { status: 204 }));

    const ad = await getCurrentAd("channel-0");
    expect(ad).toBeNull();
  });

  it("propagates the channel id into the request path", async () => {
    const fetchSpy = spyFetch(jsonResponse(200, { ad: null }));

    await getCurrentAd("channel-with-spaces");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url] = fetchSpy.mock.calls[0];
    expect(url).toBe("https://api.example.test/channels/channel-with-spaces/current");
  });
});
