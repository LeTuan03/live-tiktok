// TanStack Query keys for the restream surfaces.

export const restreamKeys = {
  destinations: ["restream", "destinations"] as const,
  outputs: (broadcastId: string) => ["restream", "outputs", broadcastId] as const,
};
