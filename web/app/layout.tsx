import type { Metadata } from "next";
import { AppQueryClientProvider } from "@/lib/query/provider";
import { ToastProvider } from "@/lib/ui/toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "SoShop",
  description: "SoShop, a demo of AI-hosted live shopping built on LemonSlice",
};

// Root layout is intentionally chrome-free: the consumer route group
// (`app/(consumer)/layout.tsx`) draws the dark consumer shell with bottom tabs
// and sets the `theme-consumer` class so the CSS variables in
// `web/styles/tokens.css` drive the surface colors.
//
// `<ToastProvider>` is mounted at the root so every surface shares the same
// toast viewport. App Router's per-segment `error.tsx` and the root
// `global-error.tsx` provide the error boundaries — see `web/app/error.tsx`
// and `web/app/global-error.tsx`.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <AppQueryClientProvider>
          <ToastProvider>{children}</ToastProvider>
        </AppQueryClientProvider>
      </body>
    </html>
  );
}
