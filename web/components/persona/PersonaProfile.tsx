"use client";

// The page route is a server component that loads the static parts (bio,
// recent shows, products) on the server and hands them down. This file
// owns the interactive shell (favorite heart, "Ask the avatar" CTA, error
// retry), so only the bits that need a hook tree are client-side.
//
// CTA wiring:
//   "Ask the avatar" should open the AskAvatar surface as a
//   modal-cover. The `@modal` parallel slot pattern is a separate change.
//   Until that lands, we navigate to `/persona?persona_id=<id>` (the
//   existing AskAvatar route) — the user lands on the same surface,
//   just without the modal-cover transition. When the modal-cover pattern ships, swap
//   the `<Link>` for a router push that targets the modal slot.

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { AIDisclosureChip } from "@/components/persona/AIDisclosureChip";
import { favoritesKeys } from "@/lib/favorites/queryKeys";
import {
  DefaultPersonaProfileService,
  type PersonaClip,
  type PersonaProduct,
  type PersonaProfile,
  type PersonaProfileService,
  describeLoadError,
} from "@/lib/persona/PersonaProfileService";

// MARK: - Props

export interface PersonaProfileProps {
  /** Server-loaded profile, or null if the server fetch failed. */
  profile: PersonaProfile | null;
  /** Server-loaded recent shows. Empty array is the loaded-but-empty state. */
  clips: PersonaClip[];
  /** Server-loaded products list. Stub today; see PersonaProfileService. */
  products: PersonaProduct[];
  /**
   * If the server fetch failed, the message is forwarded so the client
   * shell can offer a refresh hint. Reload-to-retry is sufficient for
   * the v1 surface; full client-side retry can be layered on later.
   */
  loadErrorMessage: string | null;
  /**
   * Persona id is always known (it's the route param), even when the
   * server fetch fails. The favorite toggle and the AskAvatar CTA use
   * it directly so a 5xx on the profile fetch doesn't break the CTAs.
   */
  personaId: string;
}

// MARK: - Top-level

/**
 * Authenticated persona profile shell. Caller (the page) is
 * responsible for sign-in gating and for the initial server fetch.
 */
export function PersonaProfile({
  profile,
  clips,
  products,
  loadErrorMessage,
  personaId,
}: PersonaProfileProps) {
  return (
    <article className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-6">
      {/* D5/D25: permanent disclosure chip at the top of the page. The
          chip is also rendered overlaid on AskAvatar / Feed surfaces;
          here it's part of the document flow so it can never be
          scrolled off-screen. */}
      <div className="flex justify-start">
        <AIDisclosureChip variant="avatar" className="static" />
      </div>

      {loadErrorMessage ? (
        <ErrorState message={loadErrorMessage} />
      ) : profile ? (
        <LoadedContent profile={profile} clips={clips} products={products} personaId={personaId} />
      ) : (
        <LoadingPlaceholder />
      )}
    </article>
  );
}

// MARK: - Loaded content

function LoadedContent({
  profile,
  clips,
  products,
  personaId,
}: {
  profile: PersonaProfile;
  clips: PersonaClip[];
  products: PersonaProduct[];
  personaId: string;
}) {
  return (
    <>
      <Header profile={profile} personaId={personaId} />
      {profile.traits.length > 0 ? <TraitChips traits={profile.traits} /> : null}
      <BackstorySection backstory={profile.backstory} />
      <RecentShowsSection clips={clips} />
      <ProductsSection products={products} />
      <CtaRow personaId={personaId} />
    </>
  );
}

// MARK: - Header

function Header({ profile, personaId }: { profile: PersonaProfile; personaId: string }) {
  return (
    <header className="flex items-center gap-4">
      <Avatar url={profile.avatarUrl} alt={profile.displayName} />
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-2xl font-extrabold">{profile.displayName}</h1>
        <p className="truncate text-sm text-brand-muted">{profile.handle}</p>
      </div>
      <FavoriteButton personaId={personaId} initialFavorited={profile.isFavorited} />
    </header>
  );
}

function Avatar({ url, alt }: { url: string | null; alt: string }) {
  if (!url) {
    return (
      <div
        aria-hidden
        className="h-16 w-16 flex-none rounded-full bg-gradient-to-br from-brand-accent to-brand"
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={url}
      alt={alt}
      className="h-16 w-16 flex-none rounded-full border border-white/40 object-cover"
    />
  );
}

// MARK: - Favorite

/**
 * Optimistic heart toggle. Mirrors `PersonaProfileViewModel.toggleFavorite`:
 * flip locally → fire `setFavorited` → roll back on failure. Wraps the
 * service call in a TanStack `useMutation` so the `onSettled` hook can
 * invalidate the favorites list cache — that's how cross-surface
 * consistency (this heart ↔ `/favorites`) is enforced.
 */
function FavoriteButton({
  personaId,
  initialFavorited,
}: {
  personaId: string;
  initialFavorited: boolean;
}) {
  const queryClient = useQueryClient();
  const service = useMemo<PersonaProfileService>(() => new DefaultPersonaProfileService(), []);

  const [favorited, setFavorited] = useState(initialFavorited);

  const mutation = useMutation({
    mutationFn: (next: boolean) => service.setFavorited(personaId, next),
    onMutate: (next: boolean) => {
      const previous = favorited;
      setFavorited(next);
      return { previous };
    },
    onError: (_err, _next, ctx) => {
      if (ctx) setFavorited(ctx.previous);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: favoritesKeys.list() });
    },
  });

  const inFlight = mutation.isPending;

  const onToggle = useCallback(() => {
    if (inFlight) return;
    mutation.mutate(!favorited);
  }, [favorited, inFlight, mutation]);

  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={inFlight}
      aria-pressed={favorited}
      aria-label={favorited ? "Unfavorite" : "Favorite"}
      className="inline-flex h-11 w-11 items-center justify-center rounded-full text-2xl transition disabled:opacity-50 hover:scale-110"
    >
      <span aria-hidden>{favorited ? "❤️" : "🤍"}</span>
    </button>
  );
}

// MARK: - Traits

function TraitChips({ traits }: { traits: string[] }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {traits.map((trait) => (
        <li
          key={trait}
          className="rounded-full border border-white/30 px-3 py-1 text-xs font-semibold"
        >
          {trait}
        </li>
      ))}
    </ul>
  );
}

// MARK: - Backstory

function BackstorySection({ backstory }: { backstory: string }) {
  if (!backstory) return null;
  return (
    <section aria-labelledby="backstory-heading" className="flex flex-col gap-2">
      <h2
        id="backstory-heading"
        className="text-xs font-extrabold uppercase tracking-wider text-brand-muted"
      >
        Bio
      </h2>
      <p className="whitespace-pre-line text-base text-white/90">{backstory}</p>
    </section>
  );
}

// MARK: - Recent shows

function RecentShowsSection({ clips }: { clips: PersonaClip[] }) {
  return (
    <section aria-labelledby="recent-shows-heading" className="flex flex-col gap-3">
      <h2
        id="recent-shows-heading"
        className="text-xs font-extrabold uppercase tracking-wider text-brand-muted"
      >
        Recent shows
      </h2>
      {clips.length === 0 ? (
        <p className="text-sm text-brand-muted">No recent shows yet. Start a chat to record one.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {clips.map((clip) => (
            <li
              key={clip.id}
              className="flex items-center gap-3 rounded-xl bg-white/[0.05] px-3 py-2"
            >
              <span
                aria-hidden
                className="flex h-9 w-9 flex-none items-center justify-center rounded-full bg-brand-accent/60 text-sm font-bold"
              >
                ▶
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-white/90">
                  {clip.transcript ?? "Voice clip"}
                </p>
                <p className="text-xs text-brand-muted">{formatClipMeta(clip)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function formatClipMeta(clip: PersonaClip): string {
  const duration = `${Math.round(clip.durationSeconds)}s`;
  const recorded = formatRelativeDate(clip.recordedAt);
  return recorded ? `${duration} • ${recorded}` : duration;
}

function formatRelativeDate(iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "";
  const deltaMs = Date.now() - ts;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (deltaMs < hour) return `${Math.max(1, Math.round(deltaMs / minute))}m ago`;
  if (deltaMs < day) return `${Math.round(deltaMs / hour)}h ago`;
  return `${Math.round(deltaMs / day)}d ago`;
}

// MARK: - Products

function ProductsSection({ products }: { products: PersonaProduct[] }) {
  return (
    <section aria-labelledby="products-heading" className="flex flex-col gap-3">
      <h2
        id="products-heading"
        className="text-xs font-extrabold uppercase tracking-wider text-brand-muted"
      >
        Products
      </h2>
      {products.length === 0 ? (
        <p className="text-sm text-brand-muted">
          This persona hasn&apos;t shared any products yet.
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {products.map((product) => (
            <li key={product.id} className="flex flex-col gap-2 rounded-xl bg-white/[0.05] p-2">
              <ProductImage src={product.imageUrl} alt={product.title} />
              <p className="line-clamp-2 text-sm font-semibold text-white/90">{product.title}</p>
              {product.priceLabel ? (
                <p className="text-xs text-brand-muted">{product.priceLabel}</p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ProductImage({ src, alt }: { src: string | null; alt: string }) {
  if (!src) {
    return (
      <div
        aria-hidden
        className="aspect-square w-full rounded-lg bg-gradient-to-br from-white/10 to-white/[0.04]"
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={src} alt={alt} className="aspect-square w-full rounded-lg object-cover" />
  );
}

// MARK: - CTAs

/**
 * Two CTAs side by side: primary "Ask the avatar" → AskAvatar surface,
 * and a "Favorite" mirror that scrolls focus to the heart in the
 * header for keyboard users (the heart is the primary affordance and
 * lives there for visual hierarchy reasons).
 *
 * Modal-cover dependency: when the modal-cover pattern lands, swap the
 * Ask CTA's `<Link>` for a router push targeting the `@modal` slot.
 * The surface itself doesn't change — only the navigation primitive.
 */
function CtaRow({ personaId }: { personaId: string }) {
  const askHref = `/persona?persona_id=${encodeURIComponent(personaId)}`;
  return (
    <div className="flex flex-col gap-3 pt-2 sm:flex-row">
      <Link
        href={askHref}
        className="inline-flex flex-1 items-center justify-center rounded-full bg-brand-accent px-5 py-3 text-sm font-bold text-white shadow transition hover:opacity-90"
      >
        Ask the avatar
      </Link>
      <a
        href="#favorite"
        onClick={focusFavoriteButton}
        className="inline-flex flex-1 items-center justify-center rounded-full border border-white/30 px-5 py-3 text-sm font-semibold text-white/90 transition hover:bg-white/[0.08]"
      >
        Favorite
      </a>
    </div>
  );
}

function focusFavoriteButton(event: React.MouseEvent<HTMLAnchorElement>) {
  event.preventDefault();
  const heart = document.querySelector<HTMLButtonElement>(
    'button[aria-label="Favorite"], button[aria-label="Unfavorite"]',
  );
  heart?.focus();
  heart?.scrollIntoView({ behavior: "smooth", block: "center" });
}

// MARK: - Loading / error

function LoadingPlaceholder() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true">
      <div className="h-16 w-full animate-pulse rounded-xl bg-white/[0.06]" />
      <div className="h-4 w-1/2 animate-pulse rounded bg-white/[0.06]" />
      <div className="h-32 w-full animate-pulse rounded-xl bg-white/[0.06]" />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <section className="flex flex-col items-center gap-3 rounded-xl bg-white/[0.04] px-6 py-10 text-center">
      <h2 className="text-lg font-bold">Couldn&apos;t load profile</h2>
      <p className="text-sm text-brand-muted">{message}</p>
      <p className="text-xs text-brand-muted">Refresh the page to try again.</p>
    </section>
  );
}

// MARK: - Re-exports for testing

export { describeLoadError };
