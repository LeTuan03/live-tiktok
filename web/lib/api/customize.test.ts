import { afterEach, beforeEach, describe, expect, it, type MockInstance, vi } from "vitest";
import { createCustomHost } from "./customize";

const ORIGINAL_BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
});

afterEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE;
  vi.restoreAllMocks();
});

function hostResponse(): Response {
  return new Response(
    JSON.stringify({
      room_name: "customize-1",
      livekit_url: "wss://lk",
      token: "tok",
      persona_id: "p1",
      session_id: "sess-1",
    }),
    { status: 201, headers: { "Content-Type": "application/json" } },
  );
}

function spyFetch(response: Response): MockInstance {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

function avatar(): File {
  return new File([new Uint8Array([1, 2, 3])], "avatar.png", { type: "image/png" });
}

describe("createCustomHost", () => {
  it("sends public_owner_id so the server can copy the picked Library voice", async () => {
    const fetchSpy = spyFetch(hostResponse());
    await createCustomHost({
      avatar: avatar(),
      voiceId: "lib_42",
      publicOwnerId: "owner_7",
      personality: "Warm and upbeat.",
      script: "A pitch.",
    });

    expect(fetchSpy.mock.calls[0][0]).toBe("https://api.example.test/customize/host");
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const form = init.body as FormData;
    expect(form.get("voice_id")).toBe("lib_42");
    expect(form.get("public_owner_id")).toBe("owner_7");
  });

  it("omits public_owner_id for a premade voice with no owner", async () => {
    const fetchSpy = spyFetch(hostResponse());
    await createCustomHost({
      avatar: avatar(),
      voiceId: "premade_1",
      publicOwnerId: null,
      personality: "Warm and upbeat.",
      script: "A pitch.",
    });

    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const form = init.body as FormData;
    expect(form.get("voice_id")).toBe("premade_1");
    expect(form.get("public_owner_id")).toBeNull();
  });
});
