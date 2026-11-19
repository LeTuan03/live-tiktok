// Canonical TanStack Query keys for the favorites surface.
//
// Centralising the keys here lets any mutation site
// (`PersonaProfile`, feed-card heart, etc.) call
// `queryClient.invalidateQueries({ queryKey: favoritesKeys.list() })`
// without re-deriving the tuple. Hierarchical keys follow the
// TanStack-recommended layout — see
// https://tkdodo.eu/blog/effective-react-query-keys.

export const favoritesKeys = {
  all: ["favorites"] as const,
  list: () => [...favoritesKeys.all, "list"] as const,
} as const;
