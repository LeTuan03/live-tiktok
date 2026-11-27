import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  adaptOutputs,
  createDestination,
  deleteDestination,
  listDestinations,
  listOutputs,
  switchOutput,
  updateDestination,
} from "./api";

const ORIGINAL_BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://api.test";
});

afterEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE;
  vi.restoreAllMocks();
});

const wireDestination = {
  id: "d1",
  platform: "shopee" as const,
  platform_label: "Shopee Live",
  name: "Shopee shop",
  server_url: "rtmp://push.shopee.test/live",
  has_key: true,
  key_hint: "••••9876",
  auto_on: true,
  created_at: "2026-11-21T00:00:00Z",
  updated_at: "2026-11-21T00:00:00Z",
};

const wireOutputs = {
  available: true,
  problem: null,
  outputs: [
    {
      destination: wireDestination,
      enabled: true,
      state: "live" as const,
      detail: "",
      state_at: "2026-11-21T00:00:05Z",
    },
  ],
};

function json(status: number, body: unknown): Response {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function spyFetch(...responses: Response[]): MockInstance {
  const spy = vi.spyOn(globalThis, "fetch");
  for (const response of responses) spy.mockResolvedValueOnce(response);
  return spy;
}

async function sent(
  spy: MockInstance,
  call = 0,
): Promise<{ url: string; method: string; body: unknown }> {
  const request = spy.mock.calls[call][0] as Request;
  const text = await request.clone().text();
  return { url: request.url, method: request.method, body: text ? JSON.parse(text) : null };
}

describe("adapters", () => {
  it("turns the wire shapes into camelCase domain types", () => {
    const outputs = adaptOutputs(wireOutputs);
    expect(outputs.available).toBe(true);
    expect(outputs.outputs[0]).toEqual({
      destination: {
        id: "d1",
        platform: "shopee",
        platformLabel: "Shopee Live",
        name: "Shopee shop",
        serverUrl: "rtmp://push.shopee.test/live",
        hasKey: true,
        keyHint: "••••9876",
        autoOn: true,
        createdAt: "2026-11-21T00:00:00Z",
        updatedAt: "2026-11-21T00:00:00Z",
      },
      enabled: true,
      state: "live",
      detail: "",
      stateAt: "2026-11-21T00:00:05Z",
    });
  });
});

describe("destinations", () => {
  it("lists and creates, trimming what the user typed", async () => {
    const spy = spyFetch(
      json(200, { destinations: [wireDestination] }),
      json(201, wireDestination),
    );
    const listed = await listDestinations();
    expect(listed[0].platformLabel).toBe("Shopee Live");
    await createDestination({
      platform: "shopee",
      name: " Shopee shop ",
      serverUrl: " rtmp://push.shopee.test/live ",
      streamKey: " key-1 ",
    });
    const first = await sent(spy, 0);
    expect(first.url).toBe("http://api.test/stream-destinations");
    const second = await sent(spy, 1);
    expect(second.method).toBe("POST");
    expect(second.body).toEqual({
      platform: "shopee",
      name: "Shopee shop",
      server_url: "rtmp://push.shopee.test/live",
      stream_key: "key-1",
      auto_on: false, // switched per session unless the form asks
    });
  });

  it("creates and edits a channel that switches itself on at Start", async () => {
    const spy = spyFetch(json(201, wireDestination), json(200, wireDestination));
    const made = await createDestination({
      platform: "tiktok",
      name: "TikTok",
      serverUrl: "rtmp://push-rtmp.tiktokcdn.com/game/",
      streamKey: "k",
      autoOn: true,
    });
    expect(made.autoOn).toBe(true);
    expect((await sent(spy, 0)).body).toMatchObject({ auto_on: true });
    await updateDestination("d1", { autoOn: false });
    expect((await sent(spy, 1)).body).toEqual({ auto_on: false });
  });

  it("updates only what changed and leaves the key out to keep it", async () => {
    const spy = spyFetch(json(200, wireDestination));
    await updateDestination("d1", { name: "Mới" });
    const call = await sent(spy);
    expect(call.method).toBe("PATCH");
    expect(call.url).toBe("http://api.test/stream-destinations/d1");
    expect(call.body).toEqual({ name: "Mới" });
  });

  it("keeps the server's message when it refuses", async () => {
    spyFetch(
      json(409, {
        error: { code: "conflict", message: 'Kênh đang phát trong phiên "Sale".' },
      }),
    );
    const error = await deleteDestination("d1").catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).detail).toBe('Kênh đang phát trong phiên "Sale".');
  });

  it("says the API is unreachable in Vietnamese", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await listDestinations().catch((err: unknown) => err);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).detail).toMatch(/Không kết nối được máy chủ API/);
  });
});

describe("outputs", () => {
  it("reads and switches a destination for one session", async () => {
    const spy = spyFetch(json(200, wireOutputs), json(200, wireOutputs));
    const outputs = await listOutputs("b1");
    expect(outputs.outputs[0].state).toBe("live");
    await switchOutput("b1", "d1", false);
    expect((await sent(spy, 0)).url).toBe("http://api.test/broadcasts/b1/outputs");
    const put = await sent(spy, 1);
    expect(put.method).toBe("PUT");
    expect(put.url).toBe("http://api.test/broadcasts/b1/outputs/d1");
    expect(put.body).toEqual({ enabled: false });
  });
});
