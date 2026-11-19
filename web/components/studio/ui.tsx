"use client";

// Small shared primitives for the studio surfaces (dark "studio" palette from
// tailwind.config.ts). Kept local to the studio so the consumer feed styles
// stay untouched.

import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "live" | "danger" | "ghost" | "quiet";

const VARIANT_CLASS: Record<Variant, string> = {
  primary: "bg-amber text-amber-fg hover:bg-amber-hover active:bg-amber-press",
  live: "bg-live text-live-fg hover:bg-live-hover active:bg-live-press",
  danger: "bg-danger text-white hover:brightness-110",
  ghost: "border border-studio-edge bg-studio-card text-studio-fg hover:bg-studio-hover",
  quiet: "text-studio-muted hover:bg-studio-hover hover:text-studio-fg",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md" | "lg";
  busy?: boolean;
}

export function Button({
  variant = "ghost",
  size = "md",
  busy = false,
  disabled,
  className = "",
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const sizeClass =
    size === "lg"
      ? "px-5 py-3 text-base font-semibold"
      : size === "sm"
        ? "px-2.5 py-1.5 text-xs font-medium"
        : "px-3.5 py-2 text-sm font-medium";
  return (
    <button
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex items-center justify-center gap-2 rounded-lg transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber disabled:cursor-not-allowed disabled:opacity-45 ${sizeClass} ${VARIANT_CLASS[variant]} ${className}`}
      {...rest}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`}
    />
  );
}

export const inputClass =
  "w-full rounded-lg border border-studio-border bg-studio-card px-3 py-2 text-sm text-studio-fg placeholder:text-studio-faint focus:border-amber focus:outline-none disabled:opacity-50";

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-xs font-medium text-studio-muted">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-studio-faint">{hint}</p>}
    </div>
  );
}

type Tone = "neutral" | "live" | "amber" | "danger";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "border-studio-edge bg-studio-card text-studio-muted",
  live: "border-live-edge bg-live-tint text-live",
  amber: "border-amber-edge bg-amber-tint text-amber",
  danger: "border-danger/40 bg-danger-tint text-danger",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONE_CLASS[tone]}`}
    >
      {children}
    </span>
  );
}

export function ErrorNote({ message }: { message: string | null | undefined }) {
  if (!message) return null;
  return (
    <p
      role="alert"
      className="rounded-lg border border-danger/40 bg-danger-tint px-3 py-2 text-sm text-danger"
    >
      {message}
    </p>
  );
}

export function Panel({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-2xl border border-studio-border bg-studio-panel p-4 ${className}`}>
      {(title || action) && (
        <header className="mb-3 flex items-center justify-between gap-3">
          {title && <h2 className="text-sm font-semibold text-studio-fg">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
