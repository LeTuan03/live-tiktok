// Pure helpers for the restream UI: labels for push states, the same URL and
// key checks the server makes, and what to show for an output.

import { PLATFORM_GUIDES } from "./platforms";
import type { Output, OutputState } from "./types";

type Tone = "neutral" | "live" | "amber" | "danger";

export const STATE_LABEL: Record<OutputState, string> = {
  off: "Chưa phát",
  connecting: "Đang kết nối",
  live: "Đang phát",
  retrying: "Đang thử lại",
  error: "Chưa phát được",
};

export const STATE_TONE: Record<OutputState, Tone> = {
  off: "neutral",
  connecting: "amber",
  live: "live",
  retrying: "danger",
  error: "danger",
};

/** Why `raw` is not a usable server URL, or `null`. Mirrors the server's check. */
export function serverUrlProblem(raw: string): string | null {
  const url = raw.trim();
  if (!url) return "Hãy nhập Server URL.";
  if (/\s/.test(url)) return "Server URL không được chứa khoảng trắng.";
  const match = /^(rtmps?):\/\/([^/?#]*)/i.exec(url);
  if (!match) return "Server URL phải bắt đầu bằng rtmp:// hoặc rtmps://.";
  const host = match[2].replace(/^[^@]*@/, "").replace(/:\d+$/, "");
  if (!host) return "Server URL thiếu tên máy chủ.";
  return null;
}

/** Why `raw` is not a usable stream key, or `null`. */
export function keyProblem(raw: string): string | null {
  return /\s/.test(raw.trim()) ? "Stream key không được chứa khoảng trắng." : null;
}

/** `rtmp://push.example.com:1935/live/` to `push.example.com`, for compact lists. */
export function serverHost(url: string): string {
  const match = /^[a-z]+:\/\/([^/?#]+)/i.exec(url.trim());
  return match ? match[1].replace(/^[^@]*@/, "").replace(/:\d+$/, "") : url;
}

export interface OutputView {
  label: string;
  tone: Tone;
  /** A line to show under the destination: an error, or what is missing. */
  detail: string | null;
}

/** How the control room shows one output, on air or not. */
export function describeOutput(output: Output, onAir: boolean): OutputView {
  const { destination } = output;
  const missingKey = !destination.hasKey && PLATFORM_GUIDES[destination.platform].keyRequired;
  if (!output.enabled) {
    return { label: "Tắt", tone: "neutral", detail: null };
  }
  if (!onAir) {
    return missingKey
      ? {
          label: "Thiếu stream key",
          tone: "danger",
          detail: "Dán key của phiên live ở trang Kênh phát.",
        }
      : { label: "Sẵn sàng", tone: "neutral", detail: null };
  }
  return {
    label: STATE_LABEL[output.state],
    tone: STATE_TONE[output.state],
    detail: output.detail || null,
  };
}

/** Outputs switched on for the session. */
export function enabledCount(outputs: Output[]): number {
  return outputs.filter((o) => o.enabled).length;
}

/** Whether the push states may still change on their own (worth polling). */
/** Destinations the server switches on by itself when Start is pressed. */
export function autoCount(outputs: Output[]): number {
  return outputs.filter((o) => o.destination.autoOn).length;
}

export function settling(outputs: Output[], onAir: boolean): boolean {
  return onAir || outputs.some((o) => o.state !== "off");
}
