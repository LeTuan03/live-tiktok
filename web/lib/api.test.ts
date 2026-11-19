import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import { ApiError, apiBaseUrl, authedFetch } from "./api";

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

describe("apiBaseUrl", () => {
  it("strips a trailing slash", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test/";
    expect(apiBaseUrl()).toBe("https://api.example.test");
  });

  it("throws when the env variable is unset", () => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "";
    expect(() => apiBaseUrl()).toThrow(/NEXT_PUBLIC_API_BASE_URL/);
  });
});

describe("authedFetch — request shape", () => {
  it("prefixes a relative path with the api base url", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("/healthz");
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe("https://api.example.test/healthz");
  });

  it("normalizes a path missing the leading slash", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("healthz");
    expect(fetchSpy.mock.calls[0][0]).toBe("https://api.example.test/healthz");
  });

  it("passes an absolute URL through untouched", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("https://other.test/x");
    expect(fetchSpy.mock.calls[0][0]).toBe("https://other.test/x");
  });

  it("attaches Bearer auth header when a token is supplied", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("/x", { token: "abc" });
    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer abc");
  });

  it("does not set Authorization when token is omitted", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("/x");
    const headers = new Headers((fetchSpy.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("sets Content-Type=application/json when a body is present", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("/x", { method: "POST", body: JSON.stringify({ a: 1 }) });
    const headers = new Headers((fetchSpy.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("respects an explicit Content-Type override", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("/x", {
      method: "POST",
      body: "raw",
      headers: { "Content-Type": "text/plain" },
    });
    const headers = new Headers((fetchSpy.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("Content-Type")).toBe("text/plain");
  });

  it("forces cache: no-store on every request", async () => {
    const fetchSpy = spyFetch(new Response(null, { status: 204 }));
    await authedFetch("/x");
    expect((fetchSpy.mock.calls[0][1] as RequestInit).cache).toBe("no-store");
  });

  it("returns the Response on a 2xx so callers can decode the body", async () => {
    const body = { id: "abc", count: 3 };
    spyFetch(jsonResponse(200, body));
    const res = await authedFetch("/x");
    await expect(res.json()).resolves.toEqual(body);
  });
});

describe("authedFetch — canonical error envelope", () => {
  it("parses { error: { code, message } } into ApiError fields", async () => {
    spyFetch(
      jsonResponse(503, {
        error: { code: "concurrent_rooms_exhausted", message: "Try again soon." },
      }),
    );
    await expect(authedFetch("/x")).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      detail: "Try again soon.",
      code: "concurrent_rooms_exhausted",
    });
  });

  it("captures structured extras (e.g. retry_after) from the envelope", async () => {
    spyFetch(
      jsonResponse(429, {
        error: {
          code: "rate_limited",
          message: "Slow down.",
          retry_after: 30,
        },
      }),
    );
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.code).toBe("rate_limited");
      expect(err.extra).toEqual({ retry_after: 30 });
    });
  });

  it("parses canonical 'fields' validation envelope into fieldErrors", async () => {
    spyFetch(
      jsonResponse(422, {
        error: {
          code: "validation_failed",
          message: "Bad input",
          fields: [
            { loc: "title", msg: "Required" },
            { loc: "price_cents", msg: "Must be positive" },
          ],
        },
      }),
    );
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.fieldErrors).toEqual({
        title: "Required",
        price_cents: "Must be positive",
      });
    });
  });

  it("falls back to canonical code as detail when message is missing", async () => {
    spyFetch(jsonResponse(403, { error: { code: "forbidden" } }));
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.code).toBe("forbidden");
      expect(err.detail).toBe("forbidden");
    });
  });
});

describe("authedFetch — legacy FastAPI envelope", () => {
  it("parses string `detail` into the message", async () => {
    spyFetch(jsonResponse(404, { detail: "Not found" }));
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.status).toBe(404);
      expect(err.detail).toBe("Not found");
    });
  });

  it("parses array `detail` (FastAPI 422) into fieldErrors with body-stripped path", async () => {
    spyFetch(
      jsonResponse(422, {
        detail: [
          { loc: ["body", "title"], msg: "Required" },
          { loc: ["body", "price"], msg: "Must be > 0" },
        ],
      }),
    );
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.detail).toBe("Validation failed");
      expect(err.fieldErrors).toEqual({
        title: "Required",
        price: "Must be > 0",
      });
    });
  });

  it("parses object `detail` with code/message", async () => {
    spyFetch(jsonResponse(409, { detail: { code: "conflict", message: "Already exists" } }));
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.code).toBe("conflict");
      expect(err.detail).toBe("Already exists");
    });
  });
});

describe("authedFetch — non-JSON error bodies", () => {
  it("falls back to statusText when body is not JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("oh no", { status: 500, statusText: "Server Error" }),
    );
    await authedFetch("/x").catch((err: ApiError) => {
      expect(err.status).toBe(500);
      // statusText fallback (browsers may map 500 → "Internal Server Error",
      // but our Response above sets it explicitly to "Server Error").
      expect(err.detail).toBe("Server Error");
    });
  });
});

describe("ApiError", () => {
  it("retains code, fieldErrors, and extra on the instance", () => {
    const err = new ApiError(422, "bad", {
      code: "validation_failed",
      fieldErrors: { name: "required" },
      extra: { trace_id: "t-1" },
    });
    expect(err).toBeInstanceOf(Error);
    expect(err.name).toBe("ApiError");
    expect(err.message).toBe("bad");
    expect(err.code).toBe("validation_failed");
    expect(err.fieldErrors).toEqual({ name: "required" });
    expect(err.extra).toEqual({ trace_id: "t-1" });
  });
});
