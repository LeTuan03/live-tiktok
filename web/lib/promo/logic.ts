// Pure helpers for the promo video UI (labels, limits, derived state).

import type {
  PromoLanguage,
  PromoScript,
  PromoStatus,
  PromoVideo,
  ShotBeat,
  ShotStatus,
} from "./types";

export const STATUS_LABEL: Record<PromoStatus, string> = {
  draft: "Bản nháp kịch bản",
  queued: "Đang chờ",
  rendering: "Flow đang tạo",
  assembling: "Đang ghép video",
  ready: "Hoàn tất",
  failed: "Lỗi",
};

export const SHOT_STATUS_LABEL: Record<ShotStatus, string> = {
  pending: "Chưa tạo",
  submitting: "Đang gửi",
  generating: "Flow đang tạo",
  downloading: "Đang tải về",
  done: "Xong",
  failed: "Lỗi",
};

export const BEAT_LABEL: Record<ShotBeat, string> = {
  hook: "Mở đầu",
  demo: "Dùng thử",
  benefit: "Lợi ích",
  cta: "Chốt đơn",
};

/** Max dialogue words per 8-second shot; mirrors `soshop.promo.script`. */
export const MAX_DIALOGUE_WORDS: Record<PromoLanguage, number> = { vi: 24, en: 21 };

/** Below this share of scripted words heard, a shot is flagged (`soshop.promo.qa`). */
export const SPEECH_MATCH_WARN = 0.6;

export function wordCount(text: string): number {
  return text.trim().split(/\s+/).filter(Boolean).length;
}

export function isBusy(video: Pick<PromoVideo, "status">): boolean {
  return video.status === "queued" || video.status === "rendering" || video.status === "assembling";
}

/** Shots still without a clip: what the next render will spend credits on. */
export function pendingShots(video: Pick<PromoVideo, "shots" | "shotCount">): number {
  const done = video.shots.filter((s) => s.hasClip).length;
  return Math.max(video.shotCount - done, 0);
}

/** Per-shot dialogue problems that block saving or rendering. */
export function scriptProblems(script: PromoScript, language: PromoLanguage): string[] {
  const limit = MAX_DIALOGUE_WORDS[language];
  const problems: string[] = [];
  script.shots.forEach((shot, i) => {
    const words = wordCount(shot.dialogue);
    if (words === 0) problems.push(`Cảnh ${i + 1} chưa có lời thoại.`);
    else if (words > limit)
      problems.push(`Lời thoại cảnh ${i + 1} dài ${words} từ, tối đa ${limit} từ để vừa 8 giây.`);
    if (!shot.action.trim()) problems.push(`Cảnh ${i + 1} chưa mô tả hành động.`);
  });
  return problems;
}

export function sameScript(a: PromoScript | null, b: PromoScript | null): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function formatDuration(seconds: number | null): string {
  if (seconds === null) return "";
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
