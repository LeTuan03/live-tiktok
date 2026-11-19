import { describe, expect, it } from "vitest";
import {
  controlAvailability,
  formatPrice,
  moveById,
  moveItem,
  parsePriceInput,
  pickLiveState,
  resolveMediaUrl,
  sameOrder,
} from "./logic";
import type { LiveState } from "./types";

const items = [{ id: "a" }, { id: "b" }, { id: "c" }, { id: "d" }];
const ids = (list: { id: string }[]) => list.map((i) => i.id).join("");

describe("moveItem", () => {
  it("moves an item down and up without mutating the input", () => {
    expect(ids(moveItem(items, 0, 2))).toBe("bcad");
    expect(ids(moveItem(items, 3, 1))).toBe("adbc");
    expect(ids(items)).toBe("abcd");
  });

  it("ignores out-of-range and no-op moves", () => {
    expect(ids(moveItem(items, 0, 0))).toBe("abcd");
    expect(ids(moveItem(items, -1, 2))).toBe("abcd");
    expect(ids(moveItem(items, 1, 4))).toBe("abcd");
  });
});

describe("moveById", () => {
  it("drops the dragged item at the target's position", () => {
    expect(ids(moveById(items, "a", "c"))).toBe("bcad");
    expect(ids(moveById(items, "d", "a"))).toBe("dabc");
  });

  it("leaves the list alone for unknown ids", () => {
    expect(ids(moveById(items, "x", "a"))).toBe("abcd");
  });
});

describe("sameOrder", () => {
  it("compares ids positionally", () => {
    expect(sameOrder(items, [...items])).toBe(true);
    expect(sameOrder(items, moveItem(items, 0, 1))).toBe(false);
    expect(sameOrder(items, items.slice(0, 3))).toBe(false);
  });
});

describe("resolveMediaUrl", () => {
  it("prefixes API-relative paths and keeps absolute URLs", () => {
    expect(resolveMediaUrl("/media/uploads/a.jpg", "http://localhost:8000/")).toBe(
      "http://localhost:8000/media/uploads/a.jpg",
    );
    expect(resolveMediaUrl("media/a.jpg", "http://api")).toBe("http://api/media/a.jpg");
    expect(resolveMediaUrl("https://cdn.test/a.jpg", "http://api")).toBe("https://cdn.test/a.jpg");
    expect(resolveMediaUrl(null, "http://api")).toBeNull();
    expect(resolveMediaUrl("", "http://api")).toBeNull();
  });
});

describe("price helpers", () => {
  it("formats dong without decimals", () => {
    expect(formatPrice(199000, "VND").replace(/\s/g, " ")).toMatch(/^199\.000 ₫$/);
    expect(formatPrice(null, "VND")).toBe("");
  });

  it("parses dong with thousands separators", () => {
    expect(parsePriceInput("199.000", "VND")).toBe(199000);
    expect(parsePriceInput("1,250,000", "VND")).toBe(1250000);
    expect(parsePriceInput(" 99000 ", "VND")).toBe(99000);
  });

  it("parses decimal currencies", () => {
    expect(parsePriceInput("12.5", "USD")).toBe(12.5);
    expect(parsePriceInput("12,5", "USD")).toBe(12.5);
    expect(parsePriceInput("1,299.99", "USD")).toBe(1299.99);
  });

  it("treats empty as no price and rejects junk", () => {
    expect(parsePriceInput("", "VND")).toBeNull();
    expect(parsePriceInput("abc", "VND")).toBe("invalid");
    expect(parsePriceInput("-5", "USD")).toBe("invalid");
  });
});

const state = (patch: Partial<LiveState>): LiveState => ({
  mode: "chatting",
  currentProductId: null,
  lastLine: null,
  updatedAt: "2026-11-12T10:00:00Z",
  ...patch,
});

describe("pickLiveState", () => {
  it("prefers the polled state unless the push is strictly newer", () => {
    const polled = state({ updatedAt: "2026-11-12T10:00:05Z" });
    const stale = state({ mode: "presenting", updatedAt: "2026-11-12T10:00:01Z" });
    const fresh = state({ mode: "presenting", updatedAt: "2026-11-12T10:00:09Z" });
    expect(pickLiveState(polled, stale)).toBe(polled);
    expect(pickLiveState(polled, fresh)).toBe(fresh);
    expect(pickLiveState(polled, { ...polled })).toBe(polled);
  });

  it("falls back to whichever side exists", () => {
    const only = state({});
    expect(pickLiveState(null, only)).toBe(only);
    expect(pickLiveState(only, null)).toBe(only);
    expect(pickLiveState(null, null)).toBeNull();
  });
});

describe("controlAvailability", () => {
  it("disables everything when not live or still starting", () => {
    const off = controlAvailability("draft", null, 3);
    expect(Object.values(off).every((v) => v === false)).toBe(true);
    const starting = controlAvailability("live", state({ mode: "starting" }), 3);
    expect(Object.values(starting).every((v) => v === false)).toBe(true);
    const waiting = controlAvailability("live", null, 3);
    expect(Object.values(waiting).every((v) => v === false)).toBe(true);
  });

  it("offers start while chatting, only with products", () => {
    expect(controlAvailability("live", state({}), 2)).toMatchObject({
      startProducts: true,
      pause: true,
      resume: false,
      skip: false,
      stopProducts: false,
      present: true,
      say: true,
    });
    expect(controlAvailability("live", state({}), 0)).toMatchObject({
      startProducts: false,
      present: false,
    });
  });

  it("offers pause, skip and stop while presenting", () => {
    const can = controlAvailability(
      "live",
      state({ mode: "presenting", currentProductId: "p1" }),
      2,
    );
    expect(can).toMatchObject({
      startProducts: false,
      pause: true,
      resume: false,
      skip: true,
      stopProducts: true,
    });
  });

  it("offers resume while paused; skip and stop only mid-product", () => {
    const midProduct = controlAvailability(
      "live",
      state({ mode: "paused", currentProductId: "p1" }),
      2,
    );
    expect(midProduct).toMatchObject({
      pause: false,
      resume: true,
      skip: true,
      stopProducts: true,
    });
    const midChat = controlAvailability("live", state({ mode: "paused" }), 2);
    expect(midChat).toMatchObject({ resume: true, skip: false, stopProducts: false, say: false });
  });
});
