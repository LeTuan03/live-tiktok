"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";

// Modal-cover presentation used by the `(consumer)/@modal` parallel
// slot. The chrome is intentionally thin: it owns the slide-up
// animation, the dismiss affordances (X button, Escape, browser-back
// via the intercepting route), and the body-scroll lock. Everything
// inside (Live, AskAvatar) is unchanged from its full-page form.
//
// Browser-back closing is wired by Next.js automatically because
// intercepting routes push a history entry on the way in; the X
// button and Escape both call `router.back()` to take the same exit.

interface ModalCoverProps {
  children: React.ReactNode;
  closeLabel?: string;
}

export function ModalCover({ children, closeLabel = "Close" }: ModalCoverProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    // Defer to the next frame so the transition has a starting state to
    // animate from (transform/opacity flip on first paint).
    const id = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(id);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") router.back();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 bg-black"
      style={{
        opacity: open ? 1 : 0,
        transition: "opacity 200ms ease-out",
      }}
    >
      <div
        className="theme-consumer relative h-full w-full overflow-hidden bg-[var(--surface-bg)] text-[var(--surface-fg)]"
        style={{
          transform: open ? "translateY(0)" : "translateY(100%)",
          transition: "transform 280ms cubic-bezier(0.32, 0.72, 0, 1)",
        }}
      >
        <button
          type="button"
          aria-label={closeLabel}
          onClick={() => router.back()}
          className="absolute right-3 z-10 inline-flex h-9 w-9 items-center justify-center rounded-full bg-black/50 text-white backdrop-blur transition-colors hover:bg-black/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand-coral)]"
          style={{ top: "calc(env(safe-area-inset-top) + 12px)" }}
        >
          <X size={18} strokeWidth={2.4} />
        </button>
        <div className="h-full w-full overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
