"use client";

// Control-room panel: which destinations this session is pushed to, and how
// each push is going. While the session is live or paused the server pushes
// it to every destination switched on here (TikTok Live, Shopee Live, ...).
// OBS with the on-air page stays available as the manual route.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Settings2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { listOutputs, switchOutput } from "@/lib/restream/api";
import { describeOutput, enabledCount, serverHost, settling } from "@/lib/restream/logic";
import { restreamKeys } from "@/lib/restream/queryKeys";
import type { Output, Outputs } from "@/lib/restream/types";
import { describeError } from "@/lib/studio/api";
import { useToast } from "@/lib/ui/toast";
import { Badge, Button, ErrorNote, inputClass, Panel } from "@/components/studio/ui";

export function RestreamPanel({ broadcastId, onAir }: { broadcastId: string; onAir: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = restreamKeys.outputs(broadcastId);
  const query = useQuery({
    queryKey: key,
    queryFn: () => listOutputs(broadcastId),
    refetchInterval: (q) => (settling(q.state.data?.outputs ?? [], onAir) ? 2000 : false),
  });

  const toggle = useMutation({
    mutationFn: (vars: { destinationId: string; enabled: boolean }) =>
      switchOutput(broadcastId, vars.destinationId, vars.enabled),
    onMutate: async (vars) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<Outputs>(key);
      if (previous) {
        qc.setQueryData<Outputs>(key, {
          ...previous,
          outputs: previous.outputs.map((o) =>
            o.destination.id === vars.destinationId ? { ...o, enabled: vars.enabled } : o,
          ),
        });
      }
      return { previous };
    },
    onError: (err, _vars, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
      toast.show({ kind: "error", title: "Không đổi được kênh phát", body: describeError(err) });
    },
    onSuccess: (next) => qc.setQueryData(key, next),
  });

  const data = query.data;
  const outputs = data?.outputs ?? [];
  const on = enabledCount(outputs);

  return (
    <Panel
      title="Phát lên TikTok, Shopee"
      action={
        <Link
          href="/studio/channels"
          className="inline-flex items-center gap-1 text-xs text-studio-muted hover:text-studio-fg"
        >
          <Settings2 className="h-3.5 w-3.5" /> Quản lý kênh
        </Link>
      }
    >
      {query.isLoading && <p className="text-sm text-studio-faint">Đang tải kênh phát...</p>}
      <ErrorNote message={query.isError ? describeError(query.error) : null} />
      {data && !data.available && (
        <p className="mb-3 rounded-lg border border-amber-edge bg-amber-tint px-3 py-2 text-sm text-amber">
          Server chưa đẩy luồng được: {data.problem ?? "Không rõ lý do."} Vẫn có thể phát bằng OBS ở
          dưới.
        </p>
      )}
      {data && outputs.length === 0 && (
        <div className="flex flex-col items-start gap-2 text-sm text-studio-muted">
          <p>
            Chưa có kênh phát nào. Thêm Server URL và Stream key của TikTok Live hoặc Shopee Live,
            rồi bật kênh cho phiên này.
          </p>
          <Link
            href="/studio/channels"
            className="inline-flex items-center gap-1.5 rounded-lg bg-amber px-3 py-2 text-sm font-medium text-amber-fg hover:bg-amber-hover"
          >
            Thêm kênh phát
          </Link>
        </div>
      )}
      {outputs.length > 0 && (
        <>
          <ul className="flex flex-col gap-2">
            {outputs.map((output) => (
              <OutputRow
                key={output.destination.id}
                output={output}
                onAir={onAir}
                busy={toggle.isPending && toggle.variables?.destinationId === output.destination.id}
                onToggle={(enabled) =>
                  toggle.mutate({ destinationId: output.destination.id, enabled })
                }
              />
            ))}
          </ul>
          <p className="mt-3 text-xs text-studio-faint">
            {on === 0
              ? "Bật kênh để SoShop tự đẩy phiên này lên khi bấm Bắt đầu."
              : onAir
                ? "Khi kênh báo Đang phát, vào TikTok LIVE Producer hoặc Shopee Seller Centre bấm Go LIVE (Bắt đầu phát) để người xem thấy."
                : "Bấm Bắt đầu: SoShop tự đẩy video lên các kênh đã bật. TikTok, Shopee thường cấp key mới cho mỗi phiên, hãy cập nhật key trước khi phát."}
          </p>
        </>
      )}
      <ObsRoute broadcastId={broadcastId} />
    </Panel>
  );
}

function OutputRow({
  output,
  onAir,
  busy,
  onToggle,
}: {
  output: Output;
  onAir: boolean;
  busy: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  const { destination } = output;
  const view = describeOutput(output, onAir);
  return (
    <li
      className="flex items-start gap-3 rounded-xl border border-studio-border bg-studio-card p-3"
      data-testid="Restream.output"
    >
      <button
        type="button"
        role="switch"
        aria-checked={output.enabled}
        aria-label={`Phát lên ${destination.name}`}
        disabled={busy}
        onClick={() => onToggle(!output.enabled)}
        className={`relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full transition disabled:opacity-50 ${
          output.enabled ? "bg-live" : "bg-studio-edge"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 rounded-full bg-white shadow transition ${
            output.enabled ? "translate-x-[1.125rem]" : "translate-x-0.5"
          }`}
        />
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="truncate text-sm font-semibold">{destination.name}</p>
          <span className="text-xs text-studio-faint">{destination.platformLabel}</span>
        </div>
        <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-studio-muted">
          <span className="truncate">{serverHost(destination.serverUrl)}</span>
          {destination.hasKey && (
            <span className="inline-flex items-center gap-1">
              <KeyRound className="h-3 w-3" aria-hidden /> {destination.keyHint}
            </span>
          )}
        </p>
        {view.detail && (
          <p
            className={`mt-1 text-xs ${view.tone === "danger" ? "text-danger" : "text-studio-muted"}`}
          >
            {view.detail}
          </p>
        )}
      </div>
      <Badge tone={view.tone}>{view.label}</Badge>
    </li>
  );
}

function ObsRoute({ broadcastId }: { broadcastId: string }) {
  const [copied, setCopied] = useState(false);
  const [origin, setOrigin] = useState("");
  useEffect(() => setOrigin(window.location.origin), []);
  const url = `${origin}/onair/${broadcastId}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("Sao chép đường dẫn:", url);
    }
  };
  return (
    <details className="mt-4 rounded-xl border border-studio-border bg-studio-bg p-3">
      <summary className="cursor-pointer text-sm font-medium text-studio-fg">
        Hoặc phát bằng OBS Studio
      </summary>
      <p className="mt-2 text-sm text-studio-muted">
        Màn hình phát là trang 9:16 chỉ có video, luôn đồng bộ với phòng điều khiển này. Trong OBS
        Studio, thêm nguồn <strong className="text-studio-fg">Browser</strong> với đường dẫn dưới
        đây (kích thước 1080x1920), rồi phát lên TikTok Live, Shopee Live hoặc YouTube Live bằng
        stream key của bạn. Thêm <code>?clean=1</code> để ẩn thẻ sản phẩm và nhãn LIVE. Đừng bật
        cùng một kênh ở cả SoShop và OBS.
      </p>
      <div className="mt-3 flex gap-2">
        <input readOnly value={url} className={inputClass} aria-label="Đường dẫn màn hình phát" />
        <Button onClick={copy} className="shrink-0">
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
          {copied ? "Đã chép" : "Sao chép"}
        </Button>
      </div>
    </details>
  );
}
