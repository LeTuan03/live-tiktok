"use client";

// Studio overview: the three steps of the pre-rendered livestream flow
// (products, AI videos, livestream sessions), the AI models used as hosts,
// and the older real-time AI host sessions.

import { useQuery } from "@tanstack/react-query";
import { Clapperboard, Package, Radio } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";
import { listBroadcasts } from "@/lib/broadcast/api";
import { broadcastKeys } from "@/lib/broadcast/queryKeys";
import { listCatalog, listVideos } from "@/lib/promo/api";
import { promoKeys } from "@/lib/promo/queryKeys";
import { ModelsSection } from "./ModelsSection";
import { SessionsSection } from "./SessionsSection";
import { StudioNav } from "./StudioNav";

export function StudioHome() {
  const catalog = useQuery({ queryKey: promoKeys.catalog, queryFn: listCatalog });
  const videos = useQuery({ queryKey: ["promo", "videos", "all"], queryFn: () => listVideos() });
  const broadcasts = useQuery({ queryKey: broadcastKeys.list, queryFn: listBroadcasts });
  const ready = (videos.data ?? []).filter((v) => v.status === "ready").length;
  const live = (broadcasts.data ?? []).filter((b) => b.status === "live").length;

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto flex max-w-5xl flex-col gap-5 px-4 py-6">
        <header>
          <h1 className="text-xl font-bold">Livestream bán hàng bằng video AI</h1>
          <p className="mt-1 text-sm text-studio-muted">
            Chuẩn bị sản phẩm, tạo video giới thiệu cho từng sản phẩm một lần bằng Google Flow, rồi
            ghép thành phiên livestream tự động phát và lặp lại.
          </p>
        </header>
        <ol className="grid gap-3 sm:grid-cols-3">
          <Step
            href="/studio/products"
            icon={<Package className="h-5 w-5" />}
            title="1. Sản phẩm"
            stat={catalog.data ? `${catalog.data.length} sản phẩm` : "..."}
            body="Tên, ảnh thật, mô tả, đặc điểm nổi bật, giá, nội dung cần giới thiệu."
          />
          <Step
            href="/studio/videos"
            icon={<Clapperboard className="h-5 w-5" />}
            title="2. Video AI"
            stat={videos.data ? `${ready} video sẵn sàng` : "..."}
            body="AI viết kịch bản, Google Flow dựng người mẫu cầm và dùng sản phẩm."
          />
          <Step
            href="/studio/live"
            icon={<Radio className="h-5 w-5" />}
            title="3. Phiên livestream"
            stat={
              broadcasts.data
                ? `${broadcasts.data.length} phiên${live ? ` · ${live} đang phát` : ""}`
                : "..."
            }
            body="Sắp xếp video, đặt thời lượng, bấm Bắt đầu. Tự phát và lặp đến khi dừng."
          />
        </ol>
        <ModelsSection />
        <details className="rounded-2xl border border-studio-border bg-studio-panel p-4">
          <summary className="cursor-pointer text-sm font-semibold">
            Live AI thời gian thực (thử nghiệm)
          </summary>
          <p className="mb-3 mt-2 text-xs text-studio-muted">
            Người mẫu AI nói trực tiếp bằng giọng tổng hợp và avatar dựng trên máy, không cầm được
            sản phẩm. Dùng phiên livestream ở bước 3 cho video chất lượng cao hơn.
          </p>
          <SessionsSection />
        </details>
      </div>
    </div>
  );
}

function Step({
  href,
  icon,
  title,
  stat,
  body,
}: {
  href: string;
  icon: ReactNode;
  title: string;
  stat: string;
  body: string;
}) {
  return (
    <li>
      <Link
        href={href}
        className="flex h-full flex-col gap-1.5 rounded-2xl border border-studio-border bg-studio-panel p-4 transition hover:border-amber-edge hover:bg-studio-hover"
      >
        <span className="text-amber">{icon}</span>
        <span className="text-sm font-semibold">{title}</span>
        <span className="text-xs font-medium text-live">{stat}</span>
        <span className="text-xs text-studio-muted">{body}</span>
      </Link>
    </li>
  );
}
