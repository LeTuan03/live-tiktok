// TanStack Query keys for the livestream session surfaces.

export const broadcastKeys = {
  list: ["broadcast", "list"] as const,
  detail: (id: string) => ["broadcast", "detail", id] as const,
};
