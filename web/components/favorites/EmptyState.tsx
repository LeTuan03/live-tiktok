import Link from "next/link";

/**
 * Empty state for `/favorites` when the signed-in user has no favorites
 * yet. Brand-voice copy follows acknowledge, propose, reassure.
 */
export function FavoritesEmptyState() {
  return (
    <section
      role="status"
      className="mx-auto flex min-h-[60dvh] max-w-md flex-col items-center justify-center gap-4 px-6 text-center"
    >
      <div aria-hidden className="text-4xl">
        ♡
      </div>
      <h2 className="text-2xl font-semibold">No favorites yet.</h2>
      <p className="text-brand-muted">Tap the heart on a persona to save them here.</p>
      <p className="text-sm text-brand-muted/80">
        Pin any of them to the top. You can rearrange whenever.
      </p>
      <Link
        href="/"
        className="mt-2 rounded-md bg-brand-accent px-4 py-2 text-sm font-medium text-white"
      >
        Back to Feed
      </Link>
    </section>
  );
}
