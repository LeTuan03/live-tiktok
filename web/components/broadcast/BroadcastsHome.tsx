"use client";

// Step 3 home: the livestream sessions built from pre-rendered videos.
// Create one, then open it to arrange its videos and go live.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, Radio, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { createBroadcast, deleteBroadcast, listBroadcasts } from "@/lib/broadcast/api";
import { STATUS_LABEL, STATUS_TONE, formatSpan, isOnAir } from "@/lib/broadcast/logic";
import { broadcastKeys } from "@/lib/broadcast/queryKeys";
import type { BroadcastSummary } from "@/lib/broadcast/types";
import { describeError } from "@/lib/studio/api";
import { useToast } from "@/lib/ui/toast";
import { StudioNav } from "@/components/studio/StudioNav";
import { Badge, Button, ErrorNote, inputClass, Panel } from "@/components/studio/ui";

export function BroadcastsHome() {
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const list = useQuery({
    queryKey: broadcastKeys.list,
    queryFn: listBroadcasts,
    staleTime: 0,
    refetchInterval: (q) => (q.state.data?.some((b) => isOnAir(b.status)) ? 5000 : false),
  });

  const create = useMutation({
    mutationFn: () => createBroadcast({ title }),
    onSuccess: (broadcast) => {
      qc.setQueryData(broadcastKeys.detail(broadcast.id), broadcast);
      void qc.invalidateQueries({ queryKey: broadcastKeys.list });
      router.push(`/studio/live/${broadcast.id}`);
    },
    onError: (err) => setError(describeError(err)),
  });

  const remove = useMutation({
    mutationFn: deleteBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: broadcastKeys.list }),
    onError: (err) =>
      toast.show({ kind: "error", title: "Không xóa được phiên", body: describeError(err) }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!title.trim()) {
      setError("Hãy nhập tên phiên livestream.");
      return;
    }
    create.mutate();
  };

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6">
        <header>
          <h1 className="text-xl font-bold">Phiên livestream</h1>
          <p className="mt-1 text-sm text-studio-muted">
            Ghép các video sản phẩm đã tạo thành một phiên phát trực tiếp: sắp xếp thứ tự, đặt thời
            lượng, bấm Bắt đầu. Hệ thống tự phát lần lượt và lặp lại cho đến khi bạn dừng.
          </p>
        </header>

        <Panel title="Tạo phiên mới">
          <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
            <input
              aria-label="Tên phiên livestream"
              data-testid="Broadcasts.title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={120}
              placeholder="Ví dụ: Live sale mỹ phẩm buổi tối"
              className={inputClass}
            />
            <Button
              type="submit"
              variant="primary"
              busy={create.isPending}
              className="shrink-0"
              data-testid="Broadcasts.create"
            >
              <Plus className="h-4 w-4" /> Tạo phiên
            </Button>
          </form>
          <div className="mt-2">
            <ErrorNote message={error} />
          </div>
        </Panel>

        <Panel title={`Danh sách phiên (${list.data?.length ?? 0})`}>
          {list.isLoading && <p className="text-sm text-studio-faint">Đang tải...</p>}
          <ErrorNote message={list.isError ? describeError(list.error) : null} />
          {list.data?.length === 0 && (
            <p className="text-sm text-studio-faint">
              Chưa có phiên nào. Tạo phiên đầu tiên ở trên.
            </p>
          )}
          <ul className="flex flex-col gap-2">
            {(list.data ?? []).map((b) => (
              <BroadcastRow
                key={b.id}
                broadcast={b}
                deleting={remove.isPending && remove.variables === b.id}
                onDelete={() => {
                  if (window.confirm(`Xóa phiên "${b.title}"? Các video vẫn được giữ lại.`)) {
                    remove.mutate(b.id);
                  }
                }}
              />
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function BroadcastRow({
  broadcast: b,
  deleting,
  onDelete,
}: {
  broadcast: BroadcastSummary;
  deleting: boolean;
  onDelete: () => void;
}) {
  const onAir = isOnAir(b.status);
  const loops = b.loopMode === "continuous" ? "lặp liên tục" : `lặp ${b.loopCount} vòng`;
  return (
    <li
      data-testid="Broadcasts.row"
      className="flex items-center gap-3 rounded-xl border border-studio-border bg-studio-card p-2.5"
    >
      <div className="h-16 w-9 shrink-0 overflow-hidden rounded-md bg-studio-hover">
        {b.thumbUrl && <img src={b.thumbUrl} alt="" className="h-full w-full object-cover" />}
      </div>
      <Link href={`/studio/live/${b.id}`} className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold hover:underline">{b.title}</p>
        <p className="truncate text-xs text-studio-muted">
          {b.itemCount} video
          {b.playableCount < b.itemCount
            ? ` (${b.itemCount - b.playableCount} chưa phát được)`
            : ""}
          {b.cycleS > 0 ? ` · một vòng ${formatSpan(b.cycleS)}` : ""} · {loops}
        </p>
        {onAir && b.nowPlaying && (
          <p className="truncate text-xs text-live">Đang phát: {b.nowPlaying}</p>
        )}
      </Link>
      <Badge tone={STATUS_TONE[b.status]}>
        {b.status === "live" && <Radio className="h-3 w-3" />}
        {STATUS_LABEL[b.status]}
      </Badge>
      {onAir && (
        <a
          href={`/onair/${b.id}`}
          target="_blank"
          rel="noreferrer"
          title="Mở màn hình phát"
          className="hidden rounded-lg p-1.5 text-studio-muted hover:bg-studio-hover hover:text-studio-fg sm:inline-flex"
        >
          <ExternalLink className="h-4 w-4" />
          <span className="sr-only">Mở màn hình phát</span>
        </a>
      )}
      <Link
        href={`/studio/live/${b.id}`}
        className="rounded-lg border border-studio-edge px-2.5 py-1.5 text-xs font-medium hover:bg-studio-hover"
      >
        Mở
      </Link>
      <Button
        size="sm"
        variant="quiet"
        onClick={onDelete}
        busy={deleting}
        disabled={onAir}
        title={onAir ? "Dừng phiên trước khi xóa" : undefined}
        aria-label={`Xóa phiên ${b.title}`}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </li>
  );
}
