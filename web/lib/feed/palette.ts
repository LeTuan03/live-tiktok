// Stable per-id gradient pick, used so cards have consistent colors before
// the persona view-model carries explicit colors.
//
// Lives separately from `FeedService.ts` so other surfaces (favorites,
// persona profile, etc.) can import the palette helpers without
// dragging in the swap state machine.

const FEED_PALETTES: ReadonlyArray<{ from: string; to: string }> = [
  { from: "#FF4D6D", to: "#5B2FD9" },
  { from: "#1FB87A", to: "#3B82F6" },
  { from: "#FFD23F", to: "#FF4D6D" },
  { from: "#C13C9F", to: "#0E0B1A" },
  { from: "#3B82F6", to: "#5B2FD9" },
  { from: "#F59E0B", to: "#EF4444" },
];

export function paletteForId(id: string) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  }
  return FEED_PALETTES[hash % FEED_PALETTES.length];
}

export function humanizeId(id: string): string {
  const trimmed = id.replace(/[-_]/g, " ").trim();
  if (!trimmed) return id;
  return trimmed
    .split(/\s+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

/** Synthesize a `@handle` from a display name. Keeps lowercase letters
 *  and numbers, replaces everything else with `-`, collapses repeats. */
export function handleFromName(name: string): string {
  const cleaned = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return `@${cleaned || "host"}`;
}
