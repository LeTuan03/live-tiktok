import { describe, expect, it } from "vitest";
import { adaptProduct, adaptVideo, productToWire, scriptToWire } from "./api";
import {
  formatDuration,
  isBusy,
  pendingShots,
  sameScript,
  scriptProblems,
  wordCount,
} from "./logic";
import type { PromoScript } from "./types";

const script: PromoScript = {
  angle: "a",
  productLook: "slim stick",
  setting: "bathroom",
  caption: "",
  shots: [
    {
      beat: "hook",
      action: "She holds it up.",
      camera: "close",
      emotion: "smile",
      dialogue: "Môi khô nứt mùa lạnh?",
    },
    {
      beat: "cta",
      action: "She points.",
      camera: "close",
      emotion: "smile",
      dialogue: "Mua ngay nhé",
    },
  ],
};

describe("promo logic", () => {
  it("counts words and flags dialogue that cannot fit 8 seconds", () => {
    expect(wordCount("  một  hai ba ")).toBe(3);
    expect(scriptProblems(script, "vi")).toEqual([]);
    const long = {
      ...script,
      shots: [
        { ...script.shots[0], dialogue: Array(25).fill("từ").join(" ") },
        { ...script.shots[1], action: " " },
      ],
    };
    expect(scriptProblems(long, "vi")).toEqual([
      "Lời thoại cảnh 1 dài 25 từ, tối đa 24 từ để vừa 8 giây.",
      "Cảnh 2 chưa mô tả hành động.",
    ]);
  });

  it("derives busy state, remaining shots and durations", () => {
    expect(isBusy({ status: "rendering" })).toBe(true);
    expect(isBusy({ status: "ready" })).toBe(false);
    const shots = [
      {
        index: 0,
        status: "done" as const,
        error: null,
        hasClip: true,
        transcript: null,
        speechMatch: 1,
      },
      {
        index: 1,
        status: "failed" as const,
        error: "x",
        hasClip: false,
        transcript: null,
        speechMatch: null,
      },
    ];
    expect(pendingShots({ shotCount: 3, shots })).toBe(2);
    expect(formatDuration(26.5)).toBe("0:27");
    expect(formatDuration(null)).toBe("");
    expect(sameScript(script, { ...script })).toBe(true);
    expect(sameScript(script, { ...script, setting: "kitchen" })).toBe(false);
  });
});

describe("promo api adapters", () => {
  it("adapts catalog products and builds trimmed wire bodies", () => {
    const p = adaptProduct({
      id: "1",
      sku: "SS-1",
      name: "Son",
      currency: "vnd",
      image_url: null,
      highlights: ["Dưỡng ẩm"],
    });
    expect(p).toMatchObject({
      currency: "VND",
      imageIsPlaceholder: true,
      highlights: ["Dưỡng ẩm"],
    });
    expect(productToWire({ name: " Son ", highlights: [" a ", "", "b"], imageUrl: null })).toEqual({
      name: "Son",
      highlights: ["a", "b"],
    });
  });

  it("adapts a video job with per-shot QA and round-trips the script", () => {
    const v = adaptVideo({
      id: "v1",
      product: { id: "p1", name: "Son" },
      host: { name: "Mai" },
      language: "vi",
      shot_count: 2,
      flow_model: "Veo 3.1 - Lite",
      script: scriptToWire(script),
      status: "weird",
      shots: [
        { index: 0, status: "done", has_clip: true, transcript: "Môi khô", speech_match: 0.8 },
      ],
      created_at: "2026-11-15T00:00:00Z",
      updated_at: "2026-11-15T00:00:00Z",
    });
    expect(v.status).toBe("failed");
    expect(v.script).toEqual(script);
    expect(v.shots[0]).toMatchObject({ hasClip: true, transcript: "Môi khô", speechMatch: 0.8 });
  });
});
