// TanStack Query keys for the studio surfaces, in one place so mutations can
// invalidate exactly the queries they affect.

export const studioKeys = {
  voices: ["studio", "voices"] as const,
  models: ["studio", "models"] as const,
  sessions: ["studio", "sessions"] as const,
  session: (id: string) => ["studio", "session", id] as const,
  publicSession: (id: string) => ["studio", "public", id] as const,
};
