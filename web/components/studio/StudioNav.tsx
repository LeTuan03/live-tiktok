"use client";

// Top navigation shared by the studio pages. The three steps of the
// pre-rendered livestream flow get one tab each: products, AI videos made
// with Google Flow, and the livestream sessions that play them. "Kênh phát"
// holds where sessions are pushed to (TikTok Live, Shopee Live, RTMP).

import { Cast, Clapperboard, LayoutDashboard, Package, Radio } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/studio", label: "Tổng quan", icon: LayoutDashboard, exact: true },
  { href: "/studio/products", label: "1. Sản phẩm", icon: Package, exact: false },
  { href: "/studio/videos", label: "2. Video AI", icon: Clapperboard, exact: false },
  { href: "/studio/live", label: "3. Phiên livestream", icon: Radio, exact: false },
  { href: "/studio/channels", label: "Kênh phát", icon: Cast, exact: false },
] as const;

export function StudioNav() {
  const pathname = usePathname() ?? "";
  return (
    <nav
      aria-label="Studio"
      className="sticky top-0 z-30 border-b border-studio-border bg-studio-bg/95 backdrop-blur"
    >
      <div className="mx-auto flex max-w-7xl items-center gap-1 overflow-x-auto px-4 py-2">
        <Link href="/studio" className="mr-3 shrink-0 text-sm font-bold text-studio-fg">
          SoShop <span className="text-amber">Studio</span>
        </Link>
        {TABS.map(({ href, label, icon: Icon, exact }) => {
          const current = exact ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={current ? "page" : undefined}
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition ${
                current
                  ? "bg-amber-tint font-semibold text-amber"
                  : "text-studio-muted hover:bg-studio-hover hover:text-studio-fg"
              }`}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
