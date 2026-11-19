import { FavoritesList } from "@/components/favorites/FavoritesList";

// `/favorites` is the consumer favorites tab. The backend is open
// access, so the list renders for everyone.
export default function FavoritesPage() {
  return (
    <div
      className="mx-auto w-full max-w-3xl px-4 pb-12"
      style={{ paddingTop: "calc(env(safe-area-inset-top) + 4.5rem)" }}
    >
      <FavoritesList />
    </div>
  );
}
