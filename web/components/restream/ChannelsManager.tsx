"use client";

// "Kênh phát": the RTMP destinations livestream sessions are pushed to.
// Each one is the server URL and stream key a platform hands out (TikTok
// LIVE Producer, Shopee Seller Centre, YouTube Studio). Keys go to the
// server and never come back; the list shows their last characters only.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound, Pencil, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import {
  createDestination,
  deleteDestination,
  listDestinations,
  updateDestination,
} from "@/lib/restream/api";
import { keyProblem, serverHost, serverUrlProblem } from "@/lib/restream/logic";
import { PLATFORM_GUIDES, PLATFORM_ORDER } from "@/lib/restream/platforms";
import { restreamKeys } from "@/lib/restream/queryKeys";
import type { Destination, Platform } from "@/lib/restream/types";
import { describeError } from "@/lib/studio/api";
import { useToast } from "@/lib/ui/toast";
import { StudioNav } from "@/components/studio/StudioNav";
import { Badge, Button, ErrorNote, Field, inputClass, Panel } from "@/components/studio/ui";

export function ChannelsManager() {
  const [adding, setAdding] = useState<Platform | null>(null);
  const list = useQuery({ queryKey: restreamKeys.destinations, queryFn: listDestinations });
  const destinations = list.data ?? [];

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-4 py-6">
        <header>
          <h1 className="text-xl font-bold">Kênh phát</h1>
          <p className="mt-1 text-sm text-studio-muted">
            Nơi SoShop đẩy hình của phiên livestream lên: TikTok Live, Shopee Live, YouTube Live
            hoặc bất kỳ máy chủ RTMP nào. Mỗi kênh là một cặp Server URL và Stream key do nền tảng
            cấp. Bật kênh cho từng phiên trong phòng điều khiển.
          </p>
        </header>

        <Panel title="Thêm kênh">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {PLATFORM_ORDER.map((platform) => (
              <button
                key={platform}
                type="button"
                onClick={() => setAdding(platform)}
                aria-pressed={adding === platform}
                data-testid={`Channels.add.${platform}`}
                className={`flex items-center justify-center gap-2 rounded-xl border px-3 py-3 text-sm font-semibold transition ${
                  adding === platform
                    ? "border-amber bg-amber-tint text-amber"
                    : "border-studio-edge bg-studio-card hover:bg-studio-hover"
                }`}
              >
                <Plus className="h-4 w-4" aria-hidden /> {PLATFORM_GUIDES[platform].label}
              </button>
            ))}
          </div>
          {adding && (
            <div className="mt-4">
              <DestinationForm
                key={adding}
                platform={adding}
                onDone={() => setAdding(null)}
                onCancel={() => setAdding(null)}
              />
            </div>
          )}
        </Panel>

        <Panel title={`Kênh đã lưu (${destinations.length})`}>
          {list.isLoading && <p className="text-sm text-studio-faint">Đang tải...</p>}
          <ErrorNote message={list.isError ? describeError(list.error) : null} />
          {list.isSuccess && destinations.length === 0 && (
            <p className="text-sm text-studio-faint">
              Chưa có kênh nào. Chọn nền tảng ở trên để thêm kênh đầu tiên.
            </p>
          )}
          <ul className="flex flex-col gap-2">
            {destinations.map((destination) => (
              <DestinationRow key={destination.id} destination={destination} />
            ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}

function DestinationRow({ destination }: { destination: Destination }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const remove = useMutation({
    mutationFn: () => deleteDestination(destination.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: restreamKeys.destinations }),
    onError: (err) =>
      toast.show({ kind: "error", title: "Không xóa được kênh", body: describeError(err) }),
  });
  const needsKey = !destination.hasKey && PLATFORM_GUIDES[destination.platform].keyRequired;

  return (
    <li
      className="rounded-xl border border-studio-border bg-studio-card p-3"
      data-testid="Channels.row"
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate font-semibold">{destination.name}</p>
            <Badge>{destination.platformLabel}</Badge>
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-studio-muted">
            <span className="truncate">{serverHost(destination.serverUrl)}</span>
            {destination.hasKey ? (
              <span className="inline-flex items-center gap-1">
                <KeyRound className="h-3 w-3" aria-hidden /> Key {destination.keyHint}
              </span>
            ) : needsKey ? (
              <span className="text-danger">Chưa có stream key</span>
            ) : null}
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button size="sm" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
            <Pencil className="h-3.5 w-3.5" /> {editing ? "Đóng" : "Sửa, đổi key"}
          </Button>
          <Button
            size="sm"
            variant="quiet"
            busy={remove.isPending}
            aria-label={`Xóa kênh ${destination.name}`}
            onClick={() => {
              if (window.confirm(`Xóa kênh "${destination.name}"?`)) remove.mutate();
            }}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
      {editing && (
        <div className="mt-3 border-t border-studio-border pt-3">
          <DestinationForm
            platform={destination.platform}
            existing={destination}
            onDone={() => setEditing(false)}
            onCancel={() => setEditing(false)}
          />
        </div>
      )}
    </li>
  );
}

function DestinationForm({
  platform,
  existing,
  onDone,
  onCancel,
}: {
  platform: Platform;
  existing?: Destination;
  onDone: () => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const guide = PLATFORM_GUIDES[platform];
  const id = useId();
  const [name, setName] = useState(existing?.name ?? guide.label);
  const [serverUrl, setServerUrl] = useState(existing?.serverUrl ?? guide.defaultServerUrl);
  const [streamKey, setStreamKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () =>
      existing
        ? updateDestination(existing.id, {
            name,
            serverUrl,
            // Blank keeps the stored key: it is never sent back to the browser.
            streamKey: streamKey.trim() ? streamKey : undefined,
          })
        : createDestination({ platform, name, serverUrl, streamKey }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: restreamKeys.destinations });
      void qc.invalidateQueries({ queryKey: ["restream", "outputs"] });
      onDone();
    },
    onError: (err) => setError(describeError(err)),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const problem = !name.trim()
      ? "Hãy đặt tên cho kênh phát."
      : (serverUrlProblem(serverUrl) ?? keyProblem(streamKey));
    setError(problem);
    if (!problem) save.mutate();
  };

  const keyHint = existing?.hasKey
    ? `Để trống để giữ key hiện tại (${existing.keyHint}). Dán key mới khi nền tảng cấp key khác.`
    : guide.keyRequired
      ? "Có thể lưu trước, dán key sau khi nền tảng cấp cho phiên live."
      : "Bỏ trống nếu Server URL đã gồm key.";

  return (
    <form onSubmit={submit} className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="flex flex-col gap-3">
        <Field label="Tên kênh" htmlFor={`${id}-name`}>
          <input
            id={`${id}-name`}
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            className={inputClass}
            placeholder="Ví dụ: TikTok shop chính"
          />
        </Field>
        <Field label="Server URL" htmlFor={`${id}-server`}>
          <input
            id={`${id}-server`}
            value={serverUrl}
            maxLength={500}
            onChange={(e) => setServerUrl(e.target.value)}
            className={inputClass}
            placeholder={guide.serverPlaceholder}
            autoComplete="off"
            spellCheck={false}
            data-testid="Channels.server"
          />
        </Field>
        <Field label="Stream key" htmlFor={`${id}-key`} hint={keyHint}>
          <div className="flex gap-2">
            <input
              id={`${id}-key`}
              type={showKey ? "text" : "password"}
              value={streamKey}
              maxLength={500}
              onChange={(e) => setStreamKey(e.target.value)}
              className={inputClass}
              placeholder={existing?.hasKey ? existing.keyHint : "Dán stream key"}
              autoComplete="off"
              spellCheck={false}
              data-testid="Channels.key"
            />
            <Button
              onClick={() => setShowKey((v) => !v)}
              aria-label={showKey ? "Ẩn key" : "Hiện key"}
              className="shrink-0"
            >
              {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </Button>
          </div>
        </Field>
        <ErrorNote message={error} />
        <div className="flex gap-2">
          <Button type="submit" variant="primary" busy={save.isPending} data-testid="Channels.save">
            {existing ? "Lưu thay đổi" : "Lưu kênh"}
          </Button>
          <Button variant="quiet" onClick={onCancel}>
            Hủy
          </Button>
        </div>
      </div>
      <aside className="rounded-xl border border-studio-border bg-studio-bg p-3 text-xs text-studio-muted">
        <p className="mb-2 font-semibold text-studio-fg">
          Lấy Server URL và key trên {guide.label}
        </p>
        <ol className="list-decimal space-y-1.5 pl-4">
          {guide.steps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {guide.note && <p className="mt-2 text-studio-faint">{guide.note}</p>}
        <p className="mt-2 text-studio-faint">
          Giữ kín stream key: ai có key đều phát được vào tài khoản của bạn.
        </p>
      </aside>
    </form>
  );
}
