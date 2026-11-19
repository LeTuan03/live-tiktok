// Tests for `lib/api/sdk.ts` — the runtime adapter between the
// hey-api-generated SDK and our canonical `ApiError` envelope.
//
// `configureSdkClient` is best-tested indirectly via `sdkOptions()`
// (the only call site that gets exercised in production). The
// interesting branches live in:
//
//   - `sdkOptions({ token })`: header construction with / without auth
//   - `adaptSdkError(err)`: passthrough for ApiError, Response parsing
//     for hey-api throws, fallback for arbitrary throwables

import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    apiBaseUrl: vi.fn(() => "https://api.test.local"),
  };
});

// The generated client module is the side-effect we need to control —
// stub its `setConfig` so we can assert how `configureSdkClient`
// invokes it without spinning up a real fetch client.
const setConfigMock = vi.fn();
vi.mock("@/lib/api/generated/client.gen", () => ({
  client: { setConfig: setConfigMock },
}));

const { ApiError } = await import("@/lib/api");
const { sdkOptions, adaptSdkError } = await import("./sdk");

describe("sdkOptions", () => {
  it("omits the Authorization header when no token is supplied", () => {
    const opts = sdkOptions();
    expect(opts.headers).toEqual({});
    expect(opts.throwOnError).toBe(true);
  });

  it("includes a Bearer header when a token is supplied", () => {
    const opts = sdkOptions({ token: "jwt-abc" });
    expect(opts.headers).toEqual({ Authorization: "Bearer jwt-abc" });
  });

  it("treats `token: null` as anonymous (no header set)", () => {
    const opts = sdkOptions({ token: null });
    expect(opts.headers).toEqual({});
  });

  it("forwards an AbortSignal when supplied", () => {
    const ctrl = new AbortController();
    const opts = sdkOptions({ signal: ctrl.signal });
    expect(opts.signal).toBe(ctrl.signal);
  });

  it("configures the generated client with the app's base URL", () => {
    // Don't assert call count — module-level `configured` boolean
    // makes that order-dependent across describe blocks. Just verify
    // SOMETHING called setConfig with the right baseUrl.
    sdkOptions();
    expect(setConfigMock).toHaveBeenCalledWith(
      expect.objectContaining({ baseUrl: "https://api.test.local" }),
    );
  });
});

describe("adaptSdkError", () => {
  it("returns an existing ApiError unchanged (no re-wrapping)", async () => {
    const original = new ApiError(503, "rate limited", { code: "throttled" });
    const out = await adaptSdkError(original);
    expect(out).toBe(original);
  });

  it("extracts code + message from the canonical envelope on a Response", async () => {
    const body = JSON.stringify({
      error: { code: "concurrent_rooms_exhausted", message: "Try again later" },
    });
    const res = new Response(body, {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
    const out = await adaptSdkError({ response: res });
    expect(out).toBeInstanceOf(ApiError);
    expect(out.status).toBe(503);
    expect(out.code).toBe("concurrent_rooms_exhausted");
    expect(out.detail).toBe("Try again later");
  });

  it("falls back to statusText when the body is not the canonical envelope", async () => {
    const res = new Response("not json", { status: 500, statusText: "Boom" });
    const out = await adaptSdkError({ response: res });
    expect(out.status).toBe(500);
    expect(out.detail).toBe("Boom");
    expect(out.code).toBeUndefined();
  });

  it("wraps a non-Response throwable into a status-0 ApiError", async () => {
    const out = await adaptSdkError(new Error("network down"));
    expect(out).toBeInstanceOf(ApiError);
    expect(out.status).toBe(0);
    expect(out.detail).toBe("network down");
  });

  it("handles primitive throwables with a generic message", async () => {
    const out = await adaptSdkError("oops");
    expect(out.status).toBe(0);
    expect(out.detail).toBe("Request failed");
  });
});
