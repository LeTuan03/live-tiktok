"use client";

// Whether the SoShop Flow Bridge extension is connected, so the user knows
// before spending time on a script that Flow can actually render it.

import { useQuery } from "@tanstack/react-query";
import { getFlowStatus } from "@/lib/promo/api";
import { promoKeys } from "@/lib/promo/queryKeys";
import { Badge } from "@/components/studio/ui";

export function FlowStatusBar() {
  const status = useQuery({
    queryKey: promoKeys.flowStatus,
    queryFn: getFlowStatus,
    refetchInterval: 5000,
  });
  const s = status.data;

  if (status.isError) {
    return (
      <div className="rounded-xl border border-danger/40 bg-danger-tint px-4 py-3 text-sm text-danger">
        Không kết nối được API SoShop. Kiểm tra server đã chạy chưa.
      </div>
    );
  }
  if (!s) return null;

  if (!s.connected) {
    return (
      <div
        data-testid="FlowStatus.disconnected"
        className="rounded-xl border border-amber-edge bg-amber-tint px-4 py-3 text-sm text-studio-fg"
      >
        <p className="font-semibold text-amber">Chưa kết nối Google Flow</p>
        <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-studio-muted">
          <li>
            Trong Chrome đã đăng nhập Flow, mở <code>chrome://extensions</code> và bật Developer
            mode.
          </li>
          <li>
            Bấm <strong>Load unpacked</strong>, chọn thư mục <code>server/flow_extension</code>.
          </li>
          <li>Giữ một tab flow.google.com mở. Biểu tượng extension hiện chữ ON khi đã kết nối.</li>
        </ol>
      </div>
    );
  }

  return (
    <div
      data-testid="FlowStatus.connected"
      className="flex flex-wrap items-center gap-2 rounded-xl border border-studio-border bg-studio-panel px-4 py-2.5 text-sm"
    >
      <Badge tone="live">Đã kết nối Google Flow</Badge>
      <span className="text-studio-muted">
        Extension {s.extensionVersion ?? "?"} · {s.flowTabs ?? 0} tab Flow đang mở
      </span>
      {s.runningJobId && <Badge tone="amber">Đang tạo một video</Badge>}
      {s.extensionVersion === "0.1.0" && (
        <span className="text-xs text-amber">
          Extension bản cũ: bấm reload SoShop Flow Bridge trong chrome://extensions để Flow chạy
          trong cửa sổ riêng.
        </span>
      )}
    </div>
  );
}
