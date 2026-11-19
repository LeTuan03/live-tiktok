// Spoken-word band for a customize script. Mirrors the server's canonical
// band in `soshop.agent.pitch_timing` (30-45 words ~= 12-18s at ~150 WPM).
// Kept tiny + framework-free so the form and its tests can share it.

export const WORD_COUNT_MIN = 30;
export const WORD_COUNT_MAX = 45;

export function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed ? trimmed.split(/\s+/).length : 0;
}

export type WordBandState = "empty" | "low" | "ok" | "high";

export function wordBandState(count: number): WordBandState {
  if (count === 0) return "empty";
  if (count < WORD_COUNT_MIN) return "low";
  if (count > WORD_COUNT_MAX) return "high";
  return "ok";
}
