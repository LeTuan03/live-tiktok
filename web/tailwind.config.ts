import type { Config } from "tailwindcss";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Reads the canonical brand tokens at web/lib/branding/colors.json. The same
// JSON powers web/styles/tokens.css (CSS custom properties via
// web/scripts/build-tokens.mjs). To change a brand color, edit that JSON and
// rerun `npm run build:tokens`. Don't fork hex values into component code.
type ColorToken = { value: string };
type Tokens = {
  color: {
    brand: Record<string, ColorToken>;
    neutral: Record<string, ColorToken>;
    semantic: Record<string, ColorToken>;
  };
};

const tokens: Tokens = JSON.parse(
  readFileSync(resolve(__dirname, "lib/branding/colors.json"), "utf8"),
);

const map = (group: Record<string, ColorToken>) =>
  Object.fromEntries(Object.entries(group).map(([k, v]) => [k, v.value]));

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: {
          ...map(tokens.color.brand),
          // `bg-brand` / `text-brand` (legacy) keeps mapping to dark ink so the
          // partner portal classes that already use those keep working.
          DEFAULT: tokens.color.neutral.ink.value,
          accent: tokens.color.brand.coral.value,
          muted: tokens.color.neutral.muted.value,
        },
        neutral: map(tokens.color.neutral),
        semantic: map(tokens.color.semantic),
        // CSS-var-driven surface colors; flip via .theme-consumer / .theme-partner.
        surface: {
          DEFAULT: "var(--surface-bg)",
          elevated: "var(--surface-bg-elevated)",
          fg: "var(--surface-fg)",
          "fg-muted": "var(--surface-fg-muted)",
          border: "var(--surface-border)",
        },
        // `/customize` ("LemonSlice Studio") theme — additive, page-scoped.
        // Defined here as literal hex (NOT in lib/branding/colors.json) so they
        // stay out of the global tokens.css :root and keep /customize isolated
        // from the shared brand/surface tokens. Literal hex also lets Tailwind
        // resolve alpha utilities like `bg-amber/10`. Source of truth for these
        // values: the redesign spec (color tokens, section 1).
        studio: {
          bg: "#0A0A0A", // page background, monitor "room" backdrop
          panel: "#121212", // raised panels: control desk, monitor surround
          card: "#181818", // inputs, textarea, resting sub-surfaces
          hover: "#1F1F1F", // neutral hover fill (voice rows, ghost buttons)
          border: "#242424", // hairline border — the defining element (default)
          edge: "#2E2E2E", // stronger border: hover / neutral focus
          fg: "#F4F3F0", // primary text (warm off-white)
          bright: "#FFFFFF", // max-emphasis only (rare)
          muted: "#A3A3A3", // secondary text, labels
          faint: "#6E6E6E", // placeholder, hint text
          disabled: "#4D4D4D", // disabled text/icon
        },
        // Amber — brand / selection / focus.
        amber: {
          DEFAULT: "#F7A303",
          hover: "#FFB733",
          press: "#D98A00",
          fg: "#0A0A0A", // text/icon ON an amber fill
          tint: "#241B06", // subtle fill for selected/focus backgrounds on dark
          edge: "#5A4512", // border for amber-tinted elements
        },
        // Live — ready / live / go-live (green).
        live: {
          DEFAULT: "#24FF50",
          hover: "#45FF6A",
          press: "#16E03F",
          fg: "#04140A", // text/icon ON a green fill (near-black for contrast)
          tint: "#07210E", // subtle green fill
          edge: "#0E5E26", // green hairline
        },
        // Danger — quiet, only for genuine failure on the studio theme.
        danger: {
          DEFAULT: "#D6584E", // error text/border
          tint: "#2A1311", // error banner background
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      backgroundImage: {
        "brand-gradient": "var(--brand-gradient-primary)",
        "brand-warm": "var(--brand-gradient-warm-accent)",
      },
    },
  },
  plugins: [],
};

export default config;
