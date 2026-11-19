"use client";

import { useEffect, useState } from "react";

/** Track `prefers-reduced-motion: reduce`, live. Mirrors the `matchMedia`
 *  pattern in `ProductPiP.tsx`. Returns `false` during SSR and the first
 *  client render (so hydration matches the server), then corrects on
 *  mount and on any later change to the media query.
 *
 *  Used to gate the auto-advancing product gallery: callers must not
 *  start autoplay when this returns `true` (WCAG 2.2.2 — don't move
 *  content for users who asked for stillness). */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(mql.matches);
    apply();
    mql.addEventListener("change", apply);
    return () => mql.removeEventListener("change", apply);
  }, []);
  return reduced;
}
