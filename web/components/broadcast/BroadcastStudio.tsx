"use client";

// One livestream session: playlist editor plus control room.
//
// State flow: the server's broadcast (query + event stream, `useBroadcast`)
// is the source of truth on air. Every edit and control action returns the
// updated broadcast, which replaces the cache unless a newer snapshot is
// already there. Off air, "Xem thử" runs the same timeline on a local
// playhead without touching the server.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { controlBroadcast, updateBroadcast } from "@/lib/broadcast/api";
import { applyLocal, type LocalAction } from "@/lib/broadcast/control";
import { storeBroadcast, useBroadcast, useNow } from "@/lib/broadcast/hooks";
import {
  buildTimeline,
  isOnAir,
  itemsById,
  positionAt,
  STATUS_LABEL,
  STATUS_TONE,
} from "@/lib/broadcast/logic";
import { broadcastKeys } from "@/lib/broadcast/queryKeys";
import type { Playhead } from "@/lib/broadcast/timeline";
import type { Broadcast, ControlAction } from "@/lib/broadcast/types";
import { describeError } from "@/lib/studio/api";
import { useToast } from "@/lib/ui/toast";
import { RestreamPanel } from "@/components/restream/RestreamPanel";
import { StudioNav } from "@/components/studio/StudioNav";
import { Badge, ErrorNote } from "@/components/studio/ui";
import { ControlDesk, type DeskMode } from "./ControlDesk";
import { type Edit, PlaylistEditor } from "./PlaylistEditor";
import { SchedulePreview } from "./SchedulePreview";

interface EditVars {
  label: string;
  run: () => Promise<Broadcast>;
  optimistic?: (prev: Broadcast) => Broadcast;
}

export function BroadcastStudio({ broadcastId }: { broadcastId: string }) {
  const query = useBroadcast(broadcastId);
  const broadcast = query.data;

  if (query.isError && !broadcast) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Shell>
        <ErrorNote
          message={missing ? "Không tìm thấy phiên livestream." : describeError(query.error)}
        />
      </Shell>
    );
  }
  if (!broadcast) {
    return (
      <Shell>
        <p className="text-sm text-studio-muted">Đang tải phiên livestream...</p>
      </Shell>
    );
  }
  return <Studio broadcast={broadcast} />;
}

function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto max-w-7xl px-4 py-6">{children}</div>
    </div>
  );
}

function Studio({ broadcast }: { broadcast: Broadcast }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = broadcastKeys.detail(broadcast.id);
  const now = useNow(250);
  const [muted, setMuted] = useState(false);
  const [preview, setPreview] = useState<Playhead | null>(null);

  const timeline = useMemo(() => buildTimeline(broadcast), [broadcast]);
  const items = useMemo(() => itemsById(broadcast), [broadcast]);
  const onAir = isOnAir(broadcast.playback.status);
  const mode: DeskMode = onAir ? "onair" : preview ? "preview" : "idle";
  const playhead = onAir ? broadcast.playback.playhead : preview;
  const offset = onAir ? broadcast.clockOffsetMs : 0;
  const position = positionAt(timeline, playhead, offset, now);
  const previewEnded = mode === "preview" && position === null;
  useEffect(() => {
    if (previewEnded) setPreview(null); // a preview that ran off the end stops by itself
  }, [previewEnded]);

  const mutation = useMutation({
    mutationFn: (vars: EditVars) => vars.run(),
    onMutate: async (vars) => {
      if (!vars.optimistic) return {};
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<Broadcast>(key);
      if (previous) qc.setQueryData(key, vars.optimistic(previous));
      return { previous };
    },
    onError: (err, vars, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
      toast.show({ kind: "error", title: vars.label, body: describeError(err) });
    },
    onSuccess: (next) => {
      storeBroadcast(qc, next);
      void qc.invalidateQueries({ queryKey: broadcastKeys.list });
    },
  });
  const edit: Edit = (label, run, optimistic) =>
    mutation.mutateAsync({ label, run, optimistic }).catch(() => undefined);

  const control = (action: ControlAction, itemId?: string) =>
    edit("Không thực hiện được lệnh", () => controlBroadcast(broadcast.id, action, itemId));

  const local = (action: LocalAction, itemId?: string) =>
    setPreview((head) => applyLocal(action, timeline, head, Date.now(), itemId));

  const onAction = (action: "pause" | "resume" | "next" | "prev" | "finish" | "stop") => {
    if (mode === "onair") void control(action);
    else if (action === "stop") setPreview(null);
    else local(action);
  };

  const start = async () => {
    const done = await control("start");
    if (done) setPreview(null);
  };

  const onPlayItem = (itemId: string) => {
    if (mode === "onair") void control("jump", itemId);
    else if (mode === "preview") local("jump", itemId);
    else local("start", itemId);
  };

  const current = position?.itemId ?? null;
  const progress = position?.lengthMs ? position.offsetMs / position.lengthMs : 0;
  const playLabel =
    mode === "onair" ? "Phát ngay" : mode === "preview" ? "Xem thử video này" : "Xem thử từ đây";

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5">
        <Header broadcast={broadcast} edit={edit} />
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_23rem]">
          <aside className="lg:sticky lg:top-16 lg:order-2">
            <ControlDesk
              broadcast={broadcast}
              timeline={timeline}
              items={items}
              mode={mode}
              playhead={playhead}
              clockOffsetMs={offset}
              position={position}
              nowMs={now + offset}
              muted={muted}
              onMute={setMuted}
              busy={mutation.isPending}
              onStart={start}
              onPreview={() => local("start")}
              onStopPreview={() => setPreview(null)}
              onAction={onAction}
            />
          </aside>
          <div className="flex min-w-0 flex-col gap-4 lg:order-1">
            <PlaylistEditor
              broadcast={broadcast}
              currentId={current}
              progress={progress}
              playLabel={playLabel}
              onPlay={onPlayItem}
              edit={edit}
              busy={mutation.isPending}
            />
            <SchedulePreview broadcast={broadcast} />
            <RestreamPanel broadcastId={broadcast.id} onAir={onAir} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Header({ broadcast, edit }: { broadcast: Broadcast; edit: Edit }) {
  const [title, setTitle] = useState(broadcast.title);
  const status = broadcast.playback.status;
  const commit = () => {
    const next = title.trim();
    if (!next) setTitle(broadcast.title);
    else if (next !== broadcast.title) {
      void edit("Không đổi được tên phiên", () => updateBroadcast(broadcast.id, { title: next }));
    }
  };
  return (
    <header className="flex flex-wrap items-end justify-between gap-3">
      <div className="w-full min-w-0 sm:w-auto sm:flex-1">
        <Link
          href="/studio/live"
          className="mb-1 inline-flex items-center gap-1 text-xs text-studio-muted hover:text-studio-fg"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Phiên livestream
        </Link>
        <div className="flex items-center gap-2">
          <input
            aria-label="Tên phiên livestream"
            value={title}
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            className="min-w-0 flex-1 rounded-lg border border-transparent bg-transparent px-1 py-0.5 text-xl font-bold hover:border-studio-border focus:border-amber focus:outline-none"
          />
          <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
        </div>
      </div>
      <a
        href={`/onair/${broadcast.id}`}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-center gap-1.5 rounded-lg border border-studio-edge bg-studio-card px-3 py-2 text-sm font-medium hover:bg-studio-hover"
        data-testid="Studio.output"
      >
        <ExternalLink className="h-4 w-4" /> Mở màn hình phát
      </a>
    </header>
  );
}
