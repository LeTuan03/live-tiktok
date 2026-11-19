"use client";

// Control room for one livestream session.
//
// State flow:
//   * `GET /studio/sessions/{id}` is the source of truth. It is polled every
//     2 s while the session is live.
//   * The agent also pushes its state over the room (`live_state` topic);
//     `pickLiveState` lets that push win only while it is newer than the
//     last poll, so the UI reacts instantly without drifting from the server.
//   * Joining the room: `POST .../start` returns a host token. After a page
//     reload on a live session we rejoin with `GET .../token?role=host`.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Copy,
  MessageSquare,
  Pause,
  Play,
  Radio,
  SkipForward,
  Square,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  describeError,
  endSession,
  getLiveToken,
  getSession,
  listModels,
  sendControl,
  startSession,
  updateSession,
} from "@/lib/studio/api";
import {
  controlAvailability,
  MODE_LABEL,
  pickLiveState,
  SESSION_STATUS_LABEL,
} from "@/lib/studio/logic";
import { studioKeys } from "@/lib/studio/queryKeys";
import type { ControlAction, LiveConnection, StudioSession } from "@/lib/studio/types";
import { useLiveRoom } from "@/lib/studio/useLiveRoom";
import { useToast } from "@/lib/ui/toast";
import { ChatPanel } from "./ChatPanel";
import { LiveVideo } from "./LiveVideo";
import { ProductManager } from "./ProductManager";
import { Badge, Button, ErrorNote, Field, inputClass, Panel } from "./ui";

const HOST_CHAT_NAME = "Chủ phòng";

interface ControlVars {
  action: ControlAction;
  productId?: string;
  text?: string;
}

export function ControlRoom({ sessionId }: { sessionId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = studioKeys.session(sessionId);

  const sessionQuery = useQuery({
    queryKey: key,
    queryFn: () => getSession(sessionId),
    staleTime: 0,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 1,
    refetchInterval: (query) => (query.state.data?.status === "live" ? 2000 : false),
  });
  const session = sessionQuery.data;
  const isLive = session?.status === "live";

  const [connection, setConnection] = useState<LiveConnection | null>(null);
  const [joinError, setJoinError] = useState<string | null>(null);
  const joining = useRef(false);
  const { snapshot, sendChat, startAudio } = useLiveRoom(connection);

  // Leave the room when the session stops being live; rejoin after a reload
  // (or a dropped token) while it is live. Re-evaluated on every poll.
  useEffect(() => {
    if (!session) return;
    if (session.status !== "live") {
      if (connection) setConnection(null);
      return;
    }
    if (connection || joining.current) return;
    joining.current = true;
    getLiveToken(sessionId, "host", HOST_CHAT_NAME)
      .then((next) => {
        setJoinError(null);
        setConnection(next);
      })
      .catch((err) => setJoinError(describeError(err)))
      .finally(() => {
        joining.current = false;
      });
  }, [session?.status, sessionQuery.dataUpdatedAt, connection, sessionId]);

  const setSession = (next: StudioSession) => {
    qc.setQueryData(key, next);
    void qc.invalidateQueries({ queryKey: studioKeys.sessions });
  };

  const start = useMutation({
    mutationFn: () => startSession(sessionId),
    onSuccess: ({ connection: next, session: nextSession }) => {
      setJoinError(null);
      setConnection(next);
      setSession(nextSession);
    },
  });

  const end = useMutation({
    mutationFn: () => endSession(sessionId),
    onSuccess: (nextSession) => {
      setConnection(null);
      setSession(nextSession);
    },
  });

  const control = useMutation({
    mutationFn: (vars: ControlVars) =>
      sendControl(sessionId, vars.action, { productId: vars.productId, text: vars.text }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
    onError: (err) =>
      toast.show({ kind: "error", title: "Lệnh không thực hiện được", body: describeError(err) }),
  });

  const patch = useMutation({
    mutationFn: (vars: { title?: string; modelId?: string | null; notes?: string }) =>
      updateSession(sessionId, vars),
    onSuccess: setSession,
    onError: (err) => {
      toast.show({ kind: "error", title: "Không lưu được thay đổi", body: describeError(err) });
      void qc.invalidateQueries({ queryKey: key });
    },
  });

  if (sessionQuery.isLoading) {
    return <Shell>Đang tải phiên livestream...</Shell>;
  }
  if (!session) {
    return (
      <Shell>
        <ErrorNote message={describeError(sessionQuery.error)} />
        <Link href="/studio" className="mt-3 inline-block text-sm text-amber hover:underline">
          Quay lại Studio
        </Link>
      </Shell>
    );
  }

  const live = isLive ? pickLiveState(session.live, snapshot.liveState) : null;
  const can = controlAvailability(session.status, live, session.products.length);
  const currentProduct = live?.currentProductId
    ? (session.products.find((p) => p.id === live.currentProductId) ?? null)
    : null;
  const busyAction = control.isPending ? control.variables?.action : null;
  const roomConnected = snapshot.status === "connected";
  const roomDropped =
    isLive && (snapshot.status === "failed" || snapshot.status === "disconnected");

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-5">
        <header className="flex flex-wrap items-center gap-3">
          <Link
            href="/studio"
            className="flex items-center gap-1 text-sm text-studio-muted hover:text-studio-fg"
          >
            <ArrowLeft className="h-4 w-4" /> Studio
          </Link>
          <Badge tone={isLive ? "live" : "neutral"}>
            {isLive && <Radio className="h-3 w-3" />}
            {SESSION_STATUS_LABEL[session.status]}
          </Badge>
          <SavedTextInput
            ariaLabel="Tên phiên livestream"
            testId="ControlRoom.title"
            value={session.title}
            onSave={(title) => patch.mutate({ title })}
            className="min-w-[12rem] flex-1 text-lg font-semibold"
            required
          />
        </header>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_400px]">
          {/* ------------------------------------------------ left: setup */}
          <div className="flex min-w-0 flex-col gap-4">
            <Panel title="Người mẫu và bối cảnh">
              <div className="grid gap-3 sm:grid-cols-2">
                <ModelPicker
                  session={session}
                  disabled={isLive || patch.isPending}
                  onChange={(modelId) => patch.mutate({ modelId })}
                />
                <Field
                  label="Ghi chú về shop"
                  htmlFor="session-notes"
                  hint="Tên shop, chương trình ưu đãi, chính sách đổi trả. Người mẫu dùng khi trò chuyện tự do."
                >
                  <SavedTextArea
                    id="session-notes"
                    value={session.notes}
                    onSave={(notes) => patch.mutate({ notes })}
                  />
                </Field>
              </div>
            </Panel>

            <ProductManager
              session={session}
              canPresent={can.present}
              currentProductId={live?.currentProductId ?? null}
              presentingRequestId={
                busyAction === "present" ? (control.variables?.productId ?? null) : null
              }
              onPresent={(productId) => control.mutate({ action: "present", productId })}
            />
          </div>

          {/* ---------------------------------------------- right: on air */}
          <div className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start">
            <Panel>
              <LiveVideo
                className="mx-auto aspect-[9/16] w-full max-w-[300px] rounded-xl"
                videoTrack={snapshot.videoTrack}
                audioTrack={snapshot.audioTrack}
                canPlayAudio={snapshot.canPlayAudio}
                onStartAudio={startAudio}
                placeholder={
                  <p className="text-sm text-studio-muted" data-testid="ControlRoom.placeholder">
                    {previewPlaceholder(isLive ?? false, snapshot.status)}
                  </p>
                }
              >
                {live?.lastLine && (
                  <p
                    data-testid="ControlRoom.caption"
                    className="absolute inset-x-2 bottom-2 rounded-lg bg-black/65 px-2.5 py-1.5 text-xs leading-snug text-white"
                  >
                    {live.lastLine}
                  </p>
                )}
              </LiveVideo>

              <div className="mt-3 flex flex-col gap-2">
                {isLive ? (
                  <Button
                    variant="danger"
                    size="lg"
                    data-testid="ControlRoom.end"
                    busy={end.isPending}
                    onClick={() => {
                      if (window.confirm("Kết thúc phiên livestream này?")) end.mutate();
                    }}
                  >
                    <Square className="h-4 w-4" /> Kết thúc livestream
                  </Button>
                ) : (
                  <Button
                    variant="live"
                    size="lg"
                    data-testid="ControlRoom.start"
                    busy={start.isPending}
                    disabled={!session.modelId}
                    title={session.modelId ? undefined : "Chọn người mẫu trước khi bắt đầu"}
                    onClick={() => start.mutate()}
                  >
                    <Radio className="h-4 w-4" /> Bắt đầu livestream
                  </Button>
                )}
                {!isLive && !session.modelId && (
                  <p className="text-xs text-studio-faint">
                    Chọn người mẫu AI ở khung bên trái để bắt đầu.
                  </p>
                )}
                <ErrorNote message={start.isError ? describeError(start.error) : null} />
                <ErrorNote message={end.isError ? describeError(end.error) : null} />
                <ErrorNote message={isLive ? joinError : null} />
                {isLive && snapshot.status === "failed" && (
                  <ErrorNote message={`Không vào được phòng live: ${snapshot.error ?? ""}`} />
                )}
                {roomDropped && (
                  <Button size="sm" onClick={() => setConnection(null)}>
                    Kết nối lại phòng live
                  </Button>
                )}
              </div>
            </Panel>

            <Panel title="Điều khiển người mẫu">
              <div className="mb-3 rounded-lg border border-studio-border bg-studio-card px-3 py-2 text-sm">
                <p data-testid="ControlRoom.mode" data-mode={live?.mode ?? "off"}>
                  <span className="text-studio-muted">Trạng thái: </span>
                  <span className="font-medium">
                    {live ? MODE_LABEL[live.mode] : isLive ? "Đang chờ người mẫu" : "Chưa phát"}
                  </span>
                </p>
                <p className="truncate">
                  <span className="text-studio-muted">Sản phẩm hiện tại: </span>
                  <span className="font-medium">{currentProduct?.name ?? "Không có"}</span>
                </p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Button
                  variant="primary"
                  className="col-span-2"
                  data-testid="Control.startProducts"
                  disabled={!can.startProducts || control.isPending}
                  busy={busyAction === "start_products"}
                  onClick={() => control.mutate({ action: "start_products" })}
                >
                  <Play className="h-4 w-4" /> Bắt đầu giới thiệu sản phẩm
                </Button>
                <Button
                  data-testid="Control.pause"
                  disabled={!can.pause || control.isPending}
                  busy={busyAction === "pause"}
                  onClick={() => control.mutate({ action: "pause" })}
                >
                  <Pause className="h-4 w-4" /> Tạm dừng
                </Button>
                <Button
                  data-testid="Control.resume"
                  disabled={!can.resume || control.isPending}
                  busy={busyAction === "resume"}
                  onClick={() => control.mutate({ action: "resume" })}
                >
                  <Play className="h-4 w-4" /> Tiếp tục
                </Button>
                <Button
                  className="col-span-2"
                  data-testid="Control.skip"
                  disabled={!can.skip || control.isPending}
                  busy={busyAction === "skip"}
                  onClick={() => control.mutate({ action: "skip" })}
                >
                  <SkipForward className="h-4 w-4" /> Bỏ qua sản phẩm này
                </Button>
                <Button
                  data-testid="Control.stopProducts"
                  disabled={!can.stopProducts || control.isPending}
                  busy={busyAction === "stop_products"}
                  onClick={() => control.mutate({ action: "stop_products" })}
                  className="col-span-2"
                >
                  <Undo2 className="h-4 w-4" /> Dừng giới thiệu, quay lại trò chuyện
                </Button>
              </div>
              <SayBox
                disabled={!can.say || control.isPending}
                busy={busyAction === "say"}
                onSay={(text) => control.mutateAsync({ action: "say", text })}
              />
            </Panel>

            <Panel title="Chat với người xem">
              <ChatPanel
                className="h-64"
                messages={snapshot.chat}
                connected={roomConnected}
                name={HOST_CHAT_NAME}
                onSend={sendChat}
              />
            </Panel>

            <ViewerLink sessionId={sessionId} />
          </div>
        </div>
      </div>
    </div>
  );
}

function previewPlaceholder(isLive: boolean, roomStatus: string): string {
  if (!isLive) return 'Phiên chưa phát. Nhấn "Bắt đầu livestream" để người mẫu lên sóng.';
  if (roomStatus === "failed") return "Mất kết nối tới phòng live.";
  if (roomStatus === "connected") return "Người mẫu đang vào phòng, vui lòng đợi...";
  return "Đang kết nối phòng live...";
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-[100dvh] bg-studio-bg p-6 text-sm text-studio-muted">{children}</div>
  );
}

function ModelPicker({
  session,
  disabled,
  onChange,
}: {
  session: StudioSession;
  disabled: boolean;
  onChange: (modelId: string | null) => void;
}) {
  const models = useQuery({ queryKey: studioKeys.models, queryFn: listModels, staleTime: 0 });
  const selected = models.data?.find((m) => m.id === session.modelId) ?? session.model;
  return (
    <Field label="Người mẫu AI" htmlFor="session-model">
      <select
        id="session-model"
        data-testid="ControlRoom.model"
        value={session.modelId ?? ""}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value || null)}
        className={inputClass}
      >
        <option value="">Chưa chọn</option>
        {/* Keep the current model selectable even before the list loads. */}
        {session.model && !models.data?.some((m) => m.id === session.model?.id) && (
          <option value={session.model.id}>{session.model.name}</option>
        )}
        {(models.data ?? []).map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
          </option>
        ))}
      </select>
      {models.isError && <p className="text-xs text-danger">{describeError(models.error)}</p>}
      {models.data && models.data.length === 0 && (
        <p className="text-xs text-studio-faint">
          Chưa có người mẫu.{" "}
          <Link href="/studio" className="text-amber hover:underline">
            Tạo người mẫu trong Studio
          </Link>
          .
        </p>
      )}
      {selected && selected.avatarStatus !== "ready" && (
        <p className="text-xs text-amber">
          {selected.avatarStatus === "processing"
            ? "Avatar chuyển động của người mẫu này đang được tạo."
            : "Người mẫu này chưa có avatar chuyển động, hình sẽ kém tự nhiên hơn."}{" "}
          <Link href="/studio" className="underline">
            Mở Studio
          </Link>
        </p>
      )}
      {session.status === "live" && (
        <p className="text-xs text-studio-faint">Không đổi người mẫu khi đang live.</p>
      )}
    </Field>
  );
}

/** Text input that saves on blur or Enter and follows server updates while
 *  it is not being edited. */
function SavedTextInput({
  value,
  onSave,
  ariaLabel,
  testId,
  className = "",
  required = false,
}: {
  value: string;
  onSave: (next: string) => void;
  ariaLabel: string;
  testId?: string;
  className?: string;
  required?: boolean;
}) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(value);
  }, [value]);
  const commit = () => {
    const next = draft.trim();
    if (required && !next) {
      setDraft(value);
      return;
    }
    if (next !== value) onSave(next);
  };
  return (
    <input
      aria-label={ariaLabel}
      data-testid={testId}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={() => {
        focused.current = false;
        commit();
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
      }}
      maxLength={120}
      className={`rounded-lg border border-transparent bg-transparent px-2 py-1 text-studio-fg hover:border-studio-border focus:border-amber focus:bg-studio-card focus:outline-none ${className}`}
    />
  );
}

function SavedTextArea({
  id,
  value,
  onSave,
}: {
  id: string;
  value: string;
  onSave: (next: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(value);
  }, [value]);
  return (
    <textarea
      id={id}
      data-testid="ControlRoom.notes"
      value={draft}
      rows={3}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={() => {
        focused.current = true;
      }}
      onBlur={() => {
        focused.current = false;
        if (draft !== value) onSave(draft);
      }}
      className={inputClass}
    />
  );
}

function SayBox({
  disabled,
  busy,
  onSay,
}: {
  disabled: boolean;
  busy: boolean;
  onSay: (text: string) => Promise<unknown>;
}) {
  const [text, setText] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    try {
      await onSay(trimmed);
      setText("");
    } catch {
      // The mutation's onError already showed the server message.
    }
  };
  return (
    <form onSubmit={submit} className="mt-3 flex flex-col gap-1.5">
      <label htmlFor="say-text" className="text-xs font-medium text-studio-muted">
        Nhờ người mẫu nói
      </label>
      <div className="flex gap-2">
        <input
          id="say-text"
          data-testid="Control.sayText"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ví dụ: Cảm ơn bạn Lan vừa chốt đơn"
          maxLength={400}
          disabled={disabled}
          className={inputClass}
        />
        <Button
          type="submit"
          data-testid="Control.say"
          disabled={disabled || !text.trim()}
          busy={busy}
        >
          <MessageSquare className="h-4 w-4" /> Nói
        </Button>
      </div>
    </form>
  );
}

function ViewerLink({ sessionId }: { sessionId: string }) {
  const [origin, setOrigin] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);
  const url = `${origin}/watch/${sessionId}`;
  const copy = async () => {
    setCopyError(false);
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopyError(true);
    }
  };
  return (
    <Panel title="Link cho người xem">
      <div className="flex gap-2">
        <input
          readOnly
          aria-label="Link xem livestream"
          data-testid="ControlRoom.viewerLink"
          value={url}
          onFocus={(e) => e.currentTarget.select()}
          className={inputClass}
        />
        <Button onClick={() => void copy()} aria-label="Sao chép link">
          {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </Button>
        <Link
          href={`/watch/${sessionId}`}
          target="_blank"
          className="inline-flex items-center rounded-lg border border-studio-edge bg-studio-card px-3 text-sm font-medium hover:bg-studio-hover"
        >
          Mở
        </Link>
      </div>
      {copyError && (
        <p className="mt-1 text-xs text-danger">
          Trình duyệt chặn sao chép. Hãy chọn và sao chép link thủ công.
        </p>
      )}
    </Panel>
  );
}
