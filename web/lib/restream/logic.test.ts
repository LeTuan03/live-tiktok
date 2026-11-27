import { describe, expect, it } from "vitest";
import { describeOutput, keyProblem, serverHost, serverUrlProblem, settling } from "./logic";
import type { Destination, Output } from "./types";

function destination(over: Partial<Destination> = {}): Destination {
  return {
    id: "d1",
    platform: "tiktok",
    platformLabel: "TikTok Live",
    name: "TikTok shop",
    serverUrl: "rtmp://push-rtmp.tiktokcdn.com/game/",
    hasKey: true,
    keyHint: "••••abcd",
    createdAt: "2026-11-21T00:00:00Z",
    updatedAt: "2026-11-21T00:00:00Z",
    ...over,
  };
}

function output(over: Partial<Output> = {}, dest: Partial<Destination> = {}): Output {
  return {
    destination: destination(dest),
    enabled: true,
    state: "off",
    detail: "",
    stateAt: null,
    ...over,
  };
}

describe("serverUrlProblem", () => {
  it("accepts rtmp and rtmps URLs with a host", () => {
    expect(serverUrlProblem("rtmp://push-rtmp.tiktokcdn.com/game/")).toBeNull();
    expect(serverUrlProblem("  RTMPS://live.example.com:443/app ")).toBeNull();
  });

  it("explains what is wrong, like the server", () => {
    expect(serverUrlProblem("")).toBe("Hãy nhập Server URL.");
    expect(serverUrlProblem("https://live.tiktok.com")).toMatch(/rtmp:\/\//);
    expect(serverUrlProblem("rtmp:///live")).toMatch(/máy chủ/);
    expect(serverUrlProblem("rtmp://host/live key")).toMatch(/khoảng trắng/);
  });
});

describe("keyProblem", () => {
  it("allows empty and query-string keys, refuses inner whitespace", () => {
    expect(keyProblem("")).toBeNull();
    expect(keyProblem(" stream-123?sign=x ")).toBeNull();
    expect(keyProblem("abc 123")).toMatch(/khoảng trắng/);
  });
});

describe("serverHost", () => {
  it("shows just the host of an ingest URL", () => {
    expect(serverHost("rtmp://push.example.com:1935/live/")).toBe("push.example.com");
    expect(serverHost("rtmps://user@live.example.com/app")).toBe("live.example.com");
    expect(serverHost("not a url")).toBe("not a url");
  });
});

describe("describeOutput", () => {
  it("off air: switched off, ready, or missing its key", () => {
    expect(describeOutput(output({ enabled: false }), false).label).toBe("Tắt");
    expect(describeOutput(output(), false)).toEqual({
      label: "Sẵn sàng",
      tone: "neutral",
      detail: null,
    });
    const noKey = describeOutput(output({}, { hasKey: false, keyHint: "" }), false);
    expect(noKey.label).toBe("Thiếu stream key");
    expect(noKey.tone).toBe("danger");
    // A custom URL may carry its key inside.
    const custom = describeOutput(output({}, { platform: "custom", hasKey: false }), false);
    expect(custom.label).toBe("Sẵn sàng");
  });

  it("on air: the server's state and its explanation", () => {
    expect(describeOutput(output({ state: "live" }), true)).toEqual({
      label: "Đang phát",
      tone: "live",
      detail: null,
    });
    const failing = describeOutput(
      output({ state: "retrying", detail: "Máy chủ ngắt luồng phát." }),
      true,
    );
    expect(failing).toEqual({
      label: "Đang thử lại",
      tone: "danger",
      detail: "Máy chủ ngắt luồng phát.",
    });
    expect(describeOutput(output({ state: "connecting" }), true).tone).toBe("amber");
  });
});

describe("settling", () => {
  it("polls while on air or while a push has not wound down", () => {
    expect(settling([output()], true)).toBe(true);
    expect(settling([output({ state: "live" })], false)).toBe(true);
    expect(settling([output()], false)).toBe(false);
    expect(settling([], false)).toBe(false);
  });
});
