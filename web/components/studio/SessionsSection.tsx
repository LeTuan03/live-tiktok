"use client";

// List of livestream sessions with create and delete.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import {
  createSession,
  deleteSession,
  describeError,
  listModels,
  listSessions,
} from "@/lib/studio/api";
import { SESSION_STATUS_LABEL } from "@/lib/studio/logic";
import { studioKeys } from "@/lib/studio/queryKeys";
import type { StudioSession } from "@/lib/studio/types";
import { useToast } from "@/lib/ui/toast";
import { Badge, Button, ErrorNote, inputClass, Panel } from "./ui";

export function SessionsSection() {
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [modelId, setModelId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const sessions = useQuery({
    queryKey: studioKeys.sessions,
    queryFn: listSessions,
    staleTime: 0,
    refetchInterval: (query) => (query.state.data?.some((s) => s.status === "live") ? 5000 : false),
  });
  const models = useQuery({ queryKey: studioKeys.models, queryFn: listModels, staleTime: 0 });

  const create = useMutation({
    mutationFn: createSession,
    onSuccess: (session) => {
      qc.setQueryData(studioKeys.session(session.id), session);
      void qc.invalidateQueries({ queryKey: studioKeys.sessions });
      router.push(`/studio/${session.id}`);
    },
    onError: (err) => setError(describeError(err)),
  });

  const remove = useMutation({
    mutationFn: deleteSession,
    onSuccess: () => qc.invalidateQueries({ queryKey: studioKeys.sessions }),
    onError: (err) =>
      toast.show({ kind: "error", title: "Không xóa được phiên", body: describeError(err) }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const trimmed = title.trim();
    if (!trimmed) {
      setError("Hãy nhập tên phiên livestream.");
      return;
    }
    create.mutate({ title: trimmed, modelId: modelId || null });
  };

  return (
    <Panel title="Phiên livestream">
      <form onSubmit={submit} className="mb-4 flex flex-col gap-2 sm:flex-row">
        <input
          aria-label="Tên phiên livestream"
          data-testid="Sessions.title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={120}
          placeholder="Tên phiên, ví dụ: Sale 12.12 mỹ phẩm"
          className={inputClass}
        />
        <select
          aria-label="Người mẫu"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          className={`${inputClass} sm:w-56`}
        >
          <option value="">Chọn người mẫu sau</option>
          {(models.data ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </select>
        <Button
          type="submit"
          variant="primary"
          busy={create.isPending}
          data-testid="Sessions.create"
          className="shrink-0"
        >
          <Plus className="h-4 w-4" /> Tạo phiên
        </Button>
      </form>
      <ErrorNote message={error} />

      {sessions.isLoading && <p className="text-sm text-studio-faint">Đang tải danh sách...</p>}
      {sessions.isError && <ErrorNote message={describeError(sessions.error)} />}
      {sessions.data && sessions.data.length === 0 && (
        <p className="text-sm text-studio-faint">Chưa có phiên livestream nào.</p>
      )}

      <ul className="mt-3 flex flex-col gap-2">
        {(sessions.data ?? []).map((session) => (
          <SessionRow
            key={session.id}
            session={session}
            deleting={remove.isPending && remove.variables === session.id}
            onDelete={() => {
              if (window.confirm(`Xóa phiên "${session.title}" cùng toàn bộ sản phẩm của phiên?`)) {
                remove.mutate(session.id);
              }
            }}
          />
        ))}
      </ul>
    </Panel>
  );
}

function SessionRow({
  session,
  deleting,
  onDelete,
}: {
  session: StudioSession;
  deleting: boolean;
  onDelete: () => void;
}) {
  const live = session.status === "live";
  return (
    <li
      data-testid="Sessions.row"
      className="flex items-center gap-3 rounded-xl border border-studio-border bg-studio-card px-3 py-2.5"
    >
      <Link href={`/studio/${session.id}`} className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-studio-fg hover:underline">
          {session.title}
        </p>
        <p className="truncate text-xs text-studio-muted">
          {session.products.length} sản phẩm · {session.model?.name ?? "Chưa chọn người mẫu"}
        </p>
      </Link>
      <Badge tone={live ? "live" : "neutral"}>{SESSION_STATUS_LABEL[session.status]}</Badge>
      <Link
        href={`/studio/${session.id}`}
        className="rounded-lg border border-studio-edge px-2.5 py-1.5 text-xs font-medium text-studio-fg hover:bg-studio-hover"
      >
        Mở
      </Link>
      <Button
        size="sm"
        variant="quiet"
        onClick={onDelete}
        busy={deleting}
        disabled={live}
        title={live ? "Kết thúc livestream trước khi xóa" : undefined}
        aria-label={`Xóa phiên ${session.title}`}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </li>
  );
}
