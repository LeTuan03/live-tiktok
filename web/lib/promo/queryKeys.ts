// TanStack Query keys for the promo video surfaces.

export const promoKeys = {
  catalog: ["promo", "catalog"] as const,
  flowStatus: ["promo", "flow-status"] as const,
  videos: (productId: string) => ["promo", "videos", productId] as const,
  video: (id: string) => ["promo", "video", id] as const,
};
