"use client";

// Public viewer page for one livestream session.
//
// Polls `GET /studio/sessions/{id}/public` every 3 s. When the session is
// live it fetches a viewer token and joins the LiveKit room; when the
// session ends it leaves. The current product card follows
// `live.current_product_id` (pushed over the room for instant updates,
// reconciled by the poll).

import { useQuery } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { ApiError, apiBaseUrl } from "@/lib/api";
import { describeError, getLiveToken, getPublicSession } from "@/lib/studio/api";
import {
  formatPrice,
  pickLiveState,
  PRODUCT_STATUS_LABEL,
  resolveMediaUrl,
} from "@/lib/studio/logic";
import { studioKeys } from "@/lib/studio/queryKeys";
import type { LiveConnection, PublicProduct, PublicSession } from "@/lib/studio/types";
import { useLiveRoom } from "@/lib/studio/useLiveRoom";
import { ChatPanel } from "./ChatPanel";
import { LiveVideo } from "./LiveVideo";
import { Badge, Button, inputClass } from "./ui";

const NAME_STORAGE_KEY = "soshop.watch.name";

function readStoredName(): string {
  try {
    return window.localStorage.getItem(NAME_STORAGE_KEY)?.trim() ?? "";
  } catch {
    return "";
  }
}

export function WatchView({ sessionId }: { sessionId: string }) {
  const publicQuery = useQuery({
    queryKey: studioKeys.publicSession(sessionId),
    queryFn: () => getPublicSession(sessionId),
    staleTime: 0,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 1,
    refetchInterval: 3000,
  });
  const session = publicQuery.data;
  const isLive = session?.status === "live";

  const [name, setName] = useState("");
  useEffect(() => {
    setName(readStoredName());
  }, []);
  const saveName = (next: string) => {
    setName(next);
    try {
      window.localStorage.setItem(NAME_STORAGE_KEY, next);
    } catch {
      // Private mode: the name still works for this visit.
    }
  };

  const [connection, setConnection] = useState<LiveConnection | null>(null);
  const [joinError, setJoinError] = useState<string | null>(null);
  const joining = useRef(false);
  const { snapshot, sendChat, startAudio } = useLiveRoom(connection);

  useEffect(() => {
    if (!session) return;
    if (session.status !== "live") {
      if (connection) setConnection(null);
      return;
    }
    if (connection || joining.current) return;
    joining.current = true;
    getLiveToken(sessionId, "viewer", readStoredName() || undefined)
      .then((next) => {
        setJoinError(null);
        setConnection(next);
      })
      .catch((err) => setJoinError(describeError(err)))
      .finally(() => {
        joining.current = false;
      });
  }, [session?.status, publicQuery.dataUpdatedAt, connection, sessionId]);

  if (publicQuery.isLoading) {
    return <Centered>Đang tải livestream...</Centered>;
  }
  if (!session) {
    const notFound = publicQuery.error instanceof ApiError && publicQuery.error.status === 404;
    return (
      <Centered>
        {notFound ? "Không tìm thấy phiên livestream này." : describeError(publicQuery.error)}
      </Centered>
    );
  }

  const live = isLive ? pickLiveState(session.live, snapshot.liveState) : null;
  const currentProduct = live?.currentProductId
    ? (session.products.find((p) => p.id === live.currentProductId) ?? null)
    : null;
  const roomDropped =
    isLive && (snapshot.status === "failed" || snapshot.status === "disconnected");

  return (
    <div className="min-h-[100dvh] bg-black text-white lg:flex lg:items-start lg:justify-center lg:gap-4 lg:p-4">
      <div className="relative mx-auto h-[100dvh] w-full max-w-[56.25dvh] overflow-hidden lg:mx-0 lg:h-[calc(100dvh-2rem)] lg:max-w-[calc((100dvh-2rem)*9/16)] lg:rounded-2xl">
        <LiveVideo
          className="h-full w-full"
          videoTrack={snapshot.videoTrack}
          audioTrack={snapshot.audioTrack}
          canPlayAudio={snapshot.canPlayAudio}
          onStartAudio={startAudio}
          placeholder={
            <Placeholder
              session={session}
              roomStatus={snapshot.status}
              joinError={joinError}
              roomError={snapshot.error}
            />
          }
        >
          <div className="pointer-events-none absolute inset-x-0 top-0 bg-gradient-to-b from-black/70 to-transparent p-4 pr-14">
            <div className="flex items-center gap-2">
              {isLive && (
                <span className="flex items-center gap-1 rounded bg-red-600 px-1.5 py-0.5 text-[11px] font-bold uppercase">
                  <Radio className="h-3 w-3" /> Live
                </span>
              )}
              <h1 data-testid="Watch.title" className="truncate text-base font-semibold">
                {session.title}
              </h1>
            </div>
            {session.model && <p className="mt-0.5 text-sm text-white/80">{session.model.name}</p>}
          </div>

          <div className="absolute inset-x-0 bottom-0 flex flex-col gap-2 bg-gradient-to-t from-black/80 via-black/30 to-transparent p-3 pt-10">
            {roomDropped && (
              <Button size="sm" className="self-start" onClick={() => setConnection(null)}>
                Kết nối lại
              </Button>
            )}
            {live?.lastLine && snapshot.videoTrack && (
              <p className="rounded-lg bg-black/55 px-2.5 py-1.5 text-xs leading-snug">
                {live.lastLine}
              </p>
            )}
            {currentProduct && <CurrentProductCard product={currentProduct} />}
          </div>
        </LiveVideo>
      </div>

      <aside className="flex w-full flex-col gap-4 bg-studio-bg p-4 text-studio-fg lg:h-[calc(100dvh-2rem)] lg:w-[380px] lg:rounded-2xl lg:border lg:border-studio-border">
        <section className="min-h-0 lg:flex-1 lg:overflow-y-auto">
          <h2 className="mb-2 text-sm font-semibold">
            Sản phẩm trong phiên ({session.products.length})
          </h2>
          {session.products.length === 0 ? (
            <p className="text-sm text-studio-faint">Phiên này chưa có sản phẩm.</p>
          ) : (
            <ul className="flex flex-col gap-2" data-testid="Watch.products">
              {session.products.map((product) => (
                <ProductRow
                  key={product.id}
                  product={product}
                  current={product.id === currentProduct?.id}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="flex min-h-0 flex-col lg:h-[45%]">
          <h2 className="mb-2 text-sm font-semibold">Trò chuyện</h2>
          {name ? (
            <>
              <ChatPanel
                className="h-64 lg:h-auto lg:flex-1"
                messages={snapshot.chat}
                connected={snapshot.status === "connected"}
                name={name}
                onSend={sendChat}
                disabledHint={
                  session.status === "ended"
                    ? "Phiên đã kết thúc."
                    : "Chat mở khi livestream bắt đầu."
                }
              />
              <button
                type="button"
                onClick={() => saveName("")}
                className="mt-1 self-start text-xs text-studio-faint hover:text-studio-muted"
              >
                Đang chat với tên {name}. Đổi tên
              </button>
            </>
          ) : (
            <NamePrompt onSave={saveName} />
          )}
        </section>
      </aside>
    </div>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-[100dvh] items-center justify-center bg-black p-6 text-center text-sm text-white/80">
      {children}
    </div>
  );
}

function Placeholder({
  session,
  roomStatus,
  joinError,
  roomError,
}: {
  session: PublicSession;
  roomStatus: string;
  joinError: string | null;
  roomError: string | null;
}) {
  const image = resolveMediaUrl(session.model?.imageUrl ?? null, apiBaseUrl());
  let message: string;
  if (session.status === "ended") message = "Phiên livestream đã kết thúc. Cảm ơn bạn đã theo dõi.";
  else if (session.status !== "live")
    message = "Phiên livestream chưa bắt đầu. Trang sẽ tự phát khi lên sóng.";
  else if (joinError) message = `Chưa vào được phòng live: ${joinError}`;
  else if (roomStatus === "failed") message = `Mất kết nối tới phòng live. ${roomError ?? ""}`;
  else if (roomStatus === "connected") message = "Người mẫu đang chuẩn bị lên sóng...";
  else message = "Đang kết nối livestream...";
  return (
    <div className="flex flex-col items-center gap-4" data-testid="Watch.placeholder">
      {image && (
        <img
          src={image}
          alt={session.model?.name ?? ""}
          className="h-28 w-28 rounded-full object-cover opacity-80"
        />
      )}
      <p className="max-w-xs text-sm text-white/85">{message}</p>
    </div>
  );
}

function CurrentProductCard({ product }: { product: PublicProduct }) {
  const image = resolveMediaUrl(product.imageUrl, apiBaseUrl());
  return (
    <div
      data-testid="Watch.currentProduct"
      className="flex gap-3 rounded-xl bg-white/95 p-2.5 text-black shadow-lg"
    >
      {image && <img src={image} alt="" className="h-16 w-16 shrink-0 rounded-lg object-cover" />}
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-semibold uppercase text-red-600">Đang giới thiệu</p>
        <p className="truncate text-sm font-semibold">{product.name}</p>
        {product.price !== null && (
          <p className="text-sm font-bold">{formatPrice(product.price, product.currency)}</p>
        )}
        {product.description && (
          <p className="line-clamp-2 text-xs text-black/70">{product.description}</p>
        )}
      </div>
    </div>
  );
}

function ProductRow({ product, current }: { product: PublicProduct; current: boolean }) {
  const image = resolveMediaUrl(product.imageUrl, apiBaseUrl());
  return (
    <li
      className={`flex gap-3 rounded-xl border bg-studio-card p-2 ${
        current ? "border-live-edge" : "border-studio-border"
      }`}
    >
      <div className="h-14 w-14 shrink-0 overflow-hidden rounded-lg bg-studio-hover">
        {image && <img src={image} alt="" className="h-full w-full object-cover" />}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{product.name}</p>
        <p className="text-sm font-semibold text-amber">
          {formatPrice(product.price, product.currency)}
        </p>
        {product.description && (
          <p className="line-clamp-2 text-xs text-studio-muted">{product.description}</p>
        )}
      </div>
      {(current || product.status !== "pending") && (
        <div className="shrink-0">
          <Badge tone={current ? "live" : "neutral"}>
            {current ? "Đang giới thiệu" : PRODUCT_STATUS_LABEL[product.status]}
          </Badge>
        </div>
      )}
    </li>
  );
}

function NamePrompt({ onSave }: { onSave: (name: string) => void }) {
  const [draft, setDraft] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = draft.trim();
    if (trimmed) onSave(trimmed);
  };
  return (
    <form onSubmit={submit} className="flex flex-col gap-2" data-testid="Watch.namePrompt">
      <p className="text-sm text-studio-muted">Nhập tên của bạn để tham gia trò chuyện.</p>
      <div className="flex gap-2">
        <input
          aria-label="Tên của bạn"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          maxLength={40}
          placeholder="Tên của bạn"
          className={inputClass}
        />
        <Button type="submit" variant="primary" disabled={!draft.trim()}>
          Lưu
        </Button>
      </div>
    </form>
  );
}
