"use client";

// "Thứ tự phát": the whole play order of one pass with start times, so the
// host can check the run before going live.

import { formatClock, formatSpan, schedule } from "@/lib/broadcast/logic";
import type { Broadcast } from "@/lib/broadcast/types";
import { Panel } from "@/components/studio/ui";

export function SchedulePreview({ broadcast }: { broadcast: Broadcast }) {
  const rows = schedule(broadcast);
  if (rows.length === 0) return null;
  const loops =
    broadcast.loopMode === "continuous"
      ? "Sau video cuối, quay lại video sản phẩm đầu tiên và lặp liên tục đến khi dừng."
      : `Danh sách sản phẩm phát ${broadcast.loopCount} vòng${
          broadcast.totalS ? `, cả phiên khoảng ${formatSpan(broadcast.totalS)}` : ""
        }.`;
  let n = 0;
  return (
    <Panel title="Thứ tự phát">
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm" data-testid="Schedule.table">
          <thead className="text-xs text-studio-muted">
            <tr>
              <th className="py-1.5 pr-2 font-medium">Thứ tự</th>
              <th className="py-1.5 pr-2 font-medium">Nội dung</th>
              <th className="py-1.5 pr-2 font-medium">Bắt đầu</th>
              <th className="py-1.5 font-medium">Thời lượng</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ item, startMs, lengthMs }) => {
              const label =
                item.role === "intro" ? "Mở đầu" : item.role === "outro" ? "Kết thúc" : String(++n);
              const skipped = startMs === null;
              return (
                <tr
                  key={item.id}
                  className={`border-t border-studio-border ${skipped ? "text-studio-faint line-through" : ""}`}
                >
                  <td className="py-1.5 pr-2 text-studio-muted">{label}</td>
                  <td className="max-w-[16rem] truncate py-1.5 pr-2">
                    {item.role === "product" ? `Giới thiệu ${item.title}` : item.title}
                  </td>
                  <td className="py-1.5 pr-2 tabular-nums">
                    {skipped ? "bỏ qua" : formatClock(startMs)}
                  </td>
                  <td className="py-1.5 tabular-nums">
                    {lengthMs !== null ? formatSpan(lengthMs / 1000) : "-"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-studio-muted">
        Một vòng sản phẩm:{" "}
        <strong className="text-studio-fg">{formatSpan(broadcast.cycleS)}</strong>. {loops}
      </p>
    </Panel>
  );
}
