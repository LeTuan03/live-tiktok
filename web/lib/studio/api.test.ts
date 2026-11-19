import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { ApiError } from "@/lib/api";
import {
  adaptLiveState,
  adaptPublicSession,
  adaptSession,
  createModel,
  createProduct,
  deleteProduct,
  describeError,
  errorMessageFromBody,
  getLiveToken,
  productToWire,
  reorderProducts,
  sendControl,
  startSession,
  uploadImage,
  type WireSession,
} from "./api";

const ORIGINAL_BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "http://api.test";
});

afterEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE;
  vi.restoreAllMocks();
});

function jsonResponse(status: number, body: unknown, statusText = ""): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { "Content-Type": "application/json" },
  });
}

function spyFetch(response: Response): MockInstance {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

const wireSession: WireSession = {
  id: "s1",
  title: "Sale 12.12",
  model_id: "m1",
  model: {
    id: "m1",
    name: "Linh",
    intro: null,
    personality: "vui vẻ",
    language: "vi",
    voice: "vi-VN-HoaiMyNeural",
    image_url: "/media/uploads/linh.jpg",
    avatar_status: "processing",
    avatar_progress: 0.4,
    avatar_error: null,
    created_at: "2026-11-12T09:00:00Z",
  },
  notes: null,
  status: "live",
  room_name: "live-s1",
  started_at: "2026-11-12T10:00:00Z",
  ended_at: null,
  created_at: "2026-11-12T09:30:00Z",
  live: {
    mode: "presenting",
    current_product_id: "p2",
    last_line: "Xin chào cả nhà",
    updated_at: "2026-11-12T10:01:00Z",
  },
  products: [
    {
      id: "p2",
      session_id: "s1",
      position: 1,
      name: "Son",
      price: "199000.00",
      currency: "VND",
      description: null,
      highlights: null,
      image_url: null,
      script: null,
      status: "presenting",
    },
    {
      id: "p1",
      session_id: "s1",
      position: 0,
      name: "Kem",
      price: null,
      currency: null,
      description: "d",
      highlights: "h",
      image_url: "https://cdn.test/kem.jpg",
      script: "s",
      status: "done",
    },
  ],
};

describe("adapters", () => {
  it("maps a session to camelCase, sorts products and normalizes nulls", () => {
    const session = adaptSession(wireSession);
    expect(session.modelId).toBe("m1");
    expect(session.roomName).toBe("live-s1");
    expect(session.notes).toBe("");
    expect(session.model).toMatchObject({
      intro: "",
      imageUrl: "/media/uploads/linh.jpg",
      avatarStatus: "processing",
      avatarProgress: 0.4,
    });
    expect(session.live).toEqual({
      mode: "presenting",
      currentProductId: "p2",
      lastLine: "Xin chào cả nhà",
      updatedAt: "2026-11-12T10:01:00Z",
    });
    expect(session.products.map((p) => p.id)).toEqual(["p1", "p2"]);
    // Decimal strings from Postgres NUMERIC become numbers.
    expect(session.products[1]).toMatchObject({ price: 199000, script: "", description: "" });
    expect(session.products[0]).toMatchObject({ price: null, currency: "VND", status: "done" });
  });

  it("falls back to safe enums for unknown wire values", () => {
    const session = adaptSession({
      id: "s",
      title: "t",
      status: "weird",
      products: [{ id: "p", session_id: "s", position: 0, name: "n", status: "??" }],
    });
    expect(session.status).toBe("draft");
    expect(session.products[0].status).toBe("pending");
    expect(session.live).toBeNull();
  });

  it("rejects a live state without a known mode", () => {
    expect(adaptLiveState({ mode: "dancing" })).toBeNull();
    expect(adaptLiveState(null)).toBeNull();
    expect(adaptLiveState({ mode: "chatting" })).toEqual({
      mode: "chatting",
      currentProductId: null,
      lastLine: null,
      updatedAt: null,
    });
  });

  it("maps the public session", () => {
    const pub = adaptPublicSession({
      id: "s1",
      title: "Sale",
      status: "ended",
      model: { name: "Linh", image_url: "/media/a.jpg" },
      live: null,
      products: [
        { id: "b", position: 1, name: "B", price: 5, currency: "USD", status: "skipped" },
        { id: "a", position: 0, name: "A" },
      ],
    });
    expect(pub.status).toBe("ended");
    expect(pub.model).toEqual({ name: "Linh", imageUrl: "/media/a.jpg" });
    expect(pub.products.map((p) => p.id)).toEqual(["a", "b"]);
    expect(pub.products[0]).toMatchObject({ price: null, currency: "VND", status: "pending" });
  });

  it("emits only the provided product keys in snake_case", () => {
    expect(productToWire({ name: "Son", imageUrl: null, price: 10 })).toEqual({
      name: "Son",
      image_url: null,
      price: 10,
    });
    expect(productToWire({})).toEqual({});
  });
});

describe("error handling", () => {
  it("reads the canonical envelope's message", () => {
    expect(
      errorMessageFromBody({ error: { code: "no_model", message: "Chưa chọn người mẫu" } }, "x"),
    ).toBe("Chưa chọn người mẫu");
  });

  it("appends the first field error and falls back sensibly", () => {
    expect(
      errorMessageFromBody(
        {
          error: {
            code: "validation_error",
            message: "Invalid",
            fields: [{ loc: "name", msg: "required" }],
          },
        },
        "x",
      ),
    ).toBe("Invalid: required");
    expect(errorMessageFromBody({ error: { code: "boom" } }, "x")).toBe("boom");
    expect(errorMessageFromBody({ detail: "legacy" }, "x")).toBe("legacy");
    expect(errorMessageFromBody({}, "Conflict")).toBe("Conflict");
    expect(errorMessageFromBody("nope", "Conflict")).toBe("Conflict");
  });

  it("throws ApiError carrying status and server message", async () => {
    spyFetch(jsonResponse(409, { error: { code: "not_live", message: "Phiên chưa live" } }));
    const err = await sendControl("s1", "pause").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("not_live");
    expect(describeError(err)).toBe("Phiên chưa live");
  });

  it("falls back to the status text for a non-JSON error body", async () => {
    spyFetch(new Response("<html>", { status: 502, statusText: "Bad Gateway" }));
    const err = await getLiveToken("s1", "viewer").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(describeError(err)).toBe("Bad Gateway");
  });

  it("reports an unreachable API as a readable error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await deleteProduct("p1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(describeError(err)).toMatch(/Không kết nối được máy chủ API/);
  });
});

describe("request shapes", () => {
  it("posts a product as snake_case JSON to the session", async () => {
    const fetchSpy = spyFetch(jsonResponse(200, wireSession.products?.[0]));
    await createProduct("s 1", { name: "Son", price: 199000, imageUrl: "/media/uploads/a.jpg" });
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/studio/sessions/s%201/products");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      name: "Son",
      price: 199000,
      image_url: "/media/uploads/a.jpg",
    });
  });

  it("puts the new order and returns the server's products sorted", async () => {
    const fetchSpy = spyFetch(jsonResponse(200, { products: wireSession.products }));
    const products = await reorderProducts("s1", ["p1", "p2"]);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://api.test/studio/sessions/s1/products/order");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ product_ids: ["p1", "p2"] });
    expect(products.map((p) => p.id)).toEqual(["p1", "p2"]);
  });

  it("sends control actions with optional product and text", async () => {
    // A Response body can be read once, so hand out a fresh one per call.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => jsonResponse(200, { ok: true }));
    await sendControl("s1", "present", { productId: "p2" });
    await sendControl("s1", "say", { text: "Xin chào" });
    await sendControl("s1", "skip");
    const bodies = fetchSpy.mock.calls.map(([, init]) =>
      JSON.parse((init as RequestInit).body as string),
    );
    expect(bodies).toEqual([
      { action: "present", product_id: "p2" },
      { action: "say", text: "Xin chào" },
      { action: "skip" },
    ]);
  });

  it("sends multipart without forcing a JSON content type", async () => {
    const fetchSpy = spyFetch(jsonResponse(200, { url: "/media/uploads/x.png" }));
    const file = new File(["x"], "x.png", { type: "image/png" });
    expect(await uploadImage(file)).toBe("/media/uploads/x.png");
    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.headers).toBeUndefined();
    expect((init.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("builds the model form with only the filled optional fields", async () => {
    const fetchSpy = spyFetch(jsonResponse(200, wireSession.model));
    const image = new File(["x"], "a.jpg", { type: "image/jpeg" });
    await createModel({
      name: "Linh",
      intro: "",
      personality: "vui",
      language: "vi",
      voice: "",
      image,
    });
    const form = (fetchSpy.mock.calls[0] as [string, RequestInit])[1].body as FormData;
    expect([...form.keys()].sort()).toEqual(["image", "language", "name", "personality"]);
    expect(form.get("language")).toBe("vi");
  });

  it("adapts the start response into a connection and a session", async () => {
    spyFetch(
      jsonResponse(200, {
        livekit_url: "ws://localhost:7880",
        token: "jwt",
        room_name: "live-s1",
        session: wireSession,
      }),
    );
    const { connection, session } = await startSession("s1");
    expect(connection).toEqual({
      livekitUrl: "ws://localhost:7880",
      token: "jwt",
      roomName: "live-s1",
    });
    expect(session.status).toBe("live");
  });

  it("encodes role and name on the token request", async () => {
    const fetchSpy = spyFetch(
      jsonResponse(200, { livekit_url: "ws://x", token: "t", room_name: "r" }),
    );
    await getLiveToken("s1", "viewer", "Lan Anh");
    expect(fetchSpy.mock.calls[0][0]).toBe(
      "http://api.test/studio/sessions/s1/token?role=viewer&name=Lan+Anh",
    );
  });
});
