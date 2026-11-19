import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { createPersonaSession } from "./persona";

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

describe("createPersonaSession", () => {
  const baseBody = {
    first_persona: { persona_id: "p1", script_id: "s1", product_id: null },
  };

  it("POSTs the body to /feed/session with bearer auth and json content-type", async () => {
    const fetchSpy = spyFetch(
      jsonResponse(200, {
        session_id: "sess-1",
        avatar_mode: "avatar",
        minutes_used: 0,
        minutes_limit: 10,
        seconds_until_reset: 100,
        room_name: "rm-1",
        livekit_url: "wss://lk",
        livekit_token: "tok",
        persona_id: "p1",
        dispatch_id: "d1",
        swap_mode: "single_room",
      }),
    );
    const session = await createPersonaSession("tok", baseBody);
    expect(fetchSpy.mock.calls[0][0]).toBe("https://api.example.test/feed/session");
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(baseBody);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer tok");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(session.session_id).toBe("sess-1");
    expect(session.avatar_mode).toBe("avatar");
  });

  it("returns the non_avatar mode body unchanged when the daily cap is hit", async () => {
    spyFetch(
      jsonResponse(200, {
        session_id: "sess-2",
        avatar_mode: "non_avatar",
        minutes_used: 10,
        minutes_limit: 10,
        seconds_until_reset: 50,
        room_name: null,
        livekit_url: null,
        livekit_token: null,
        persona_id: null,
        dispatch_id: null,
        swap_mode: null,
      }),
    );
    const session = await createPersonaSession("tok", baseBody);
    expect(session.avatar_mode).toBe("non_avatar");
    expect(session.livekit_url).toBeNull();
    expect(session.livekit_token).toBeNull();
  });

  it("propagates the canonical 503 error envelope with retry_after extras", async () => {
    spyFetch(
      jsonResponse(503, {
        error: {
          code: "concurrent_rooms_exhausted",
          message: "Try again shortly.",
          retry_after: 12,
        },
      }),
    );
    await expect(createPersonaSession("tok", baseBody)).rejects.toMatchObject({
      status: 503,
      code: "concurrent_rooms_exhausted",
      extra: { retry_after: 12 },
    });
  });
});
