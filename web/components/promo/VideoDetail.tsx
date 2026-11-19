"use client";

// Steps 3 to 5 for one video: review and edit the AI script (no credits
// spent yet), send it to Google Flow, follow each shot, then watch the
// stitched result with captions and redo any shot that came out wrong.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Download, RefreshCw, RotateCcw, Save, Square, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
  cancelVideo,
  deleteVideo,
  getVideo,
  regenerateScript,
  regenerateShot,
  renderVideo,
  saveScript,
  videoUrls,
} from "@/lib/promo/api";
import {
  BEAT_LABEL,
  MAX_DIALOGUE_WORDS,
  SHOT_STATUS_LABEL,
  SPEECH_MATCH_WARN,
  STATUS_LABEL,
  formatDuration,
  isBusy,
  pendingShots,
  sameScript,
  scriptProblems,
  wordCount,
} from "@/lib/promo/logic";
import { promoKeys } from "@/lib/promo/queryKeys";
import type { PromoScript, PromoShot, PromoVideo, ShotState } from "@/lib/promo/types";
import { describeError } from "@/lib/studio/api";
import { useToast } from "@/lib/ui/toast";
import {
  Badge,
  Button,
  ErrorNote,
  Field,
  inputClass,
  Panel,
  Spinner,
} from "@/components/studio/ui";

export function VideoDetail({ videoId, onDeleted }: { videoId: string; onDeleted: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const query = useQuery({
    queryKey: promoKeys.video(videoId),
    queryFn: () => getVideo(videoId),
    refetchInterval: (q) => (q.state.data && isBusy(q.state.data) ? 3000 : false),
  });
  const video = query.data;
  const [draft, setDraft] = useState<PromoScript | null>(null);

  // Take the server's script whenever it changes (new script, saved edit).
  const serverScript = video?.script ?? null;
  useEffect(() => {
    setDraft(serverScript);
  }, [JSON.stringify(serverScript)]);

  const apply = (next: PromoVideo) => {
    qc.setQueryData(promoKeys.video(next.id), next);
    qc.invalidateQueries({ queryKey: promoKeys.videos(next.productId) });
    qc.invalidateQueries({ queryKey: promoKeys.flowStatus });
  };
  const fail = (title: string) => (err: unknown) =>
    toast.show({ kind: "error", title, body: describeError(err) });

  const save = useMutation({
    mutationFn: (script: PromoScript) => saveScript(videoId, script),
    onSuccess: apply,
    onError: fail("Không lưu được kịch bản"),
  });
  const rewrite = useMutation({
    mutationFn: () => regenerateScript(videoId),
    onSuccess: apply,
    onError: fail("AI chưa viết lại được"),
  });
  const render = useMutation({
    mutationFn: async () => {
      if (draft && !sameScript(draft, serverScript)) await saveScript(videoId, draft);
      return renderVideo(videoId);
    },
    onSuccess: apply,
    onError: fail("Không gửi được cho Google Flow"),
  });
  const redoShot = useMutation({
    mutationFn: (index: number) => regenerateShot(videoId, index),
    onSuccess: apply,
    onError: fail("Không tạo lại được cảnh"),
  });
  const cancel = useMutation({
    mutationFn: () => cancelVideo(videoId),
    onSuccess: apply,
    onError: fail("Không hủy được"),
  });
  const remove = useMutation({
    mutationFn: () => deleteVideo(videoId),
    onSuccess: () => {
      if (video) qc.invalidateQueries({ queryKey: promoKeys.videos(video.productId) });
      onDeleted();
    },
    onError: fail("Không xóa được video"),
  });

  if (query.isLoading) {
    return (
      <Panel>
        <p className="flex items-center gap-2 text-sm text-studio-muted">
          <Spinner /> Đang tải video...
        </p>
      </Panel>
    );
  }
  if (!video || !draft)
    return <ErrorNote message={query.error ? describeError(query.error) : null} />;

  const busy = isBusy(video);
  const dirty = !sameScript(draft, serverScript);
  const problems = scriptProblems(draft, video.language);
  const remaining = pendingShots(video);
  const urls = videoUrls(video);

  const setShot = (index: number, patch: Partial<PromoShot>) =>
    setDraft((prev) =>
      prev
        ? { ...prev, shots: prev.shots.map((s, i) => (i === index ? { ...s, ...patch } : s)) }
        : prev,
    );

  return (
    <div className="flex flex-col gap-4" data-testid="VideoDetail">
      <Panel
        title="4. Kịch bản và tạo video bằng Google Flow"
        action={
          <Badge
            tone={
              video.status === "ready" ? "live" : video.status === "failed" ? "danger" : "amber"
            }
          >
            {STATUS_LABEL[video.status]}
          </Badge>
        }
      >
        <p className="mb-3 text-xs text-studio-muted">
          {video.hostName} giới thiệu {video.productName} · {video.flowModel} · {video.shotCount}{" "}
          cảnh 8 giây. Kiểm tra lời thoại đúng sự thật về sản phẩm trước khi tạo.
        </p>

        {busy && (
          <Progress video={video} onCancel={() => cancel.mutate()} cancelling={cancel.isPending} />
        )}
        {video.status === "failed" && video.error && (
          <div className="mb-3">
            <ErrorNote message={video.error} />
            <a
              href={urls.debug}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-block text-xs text-studio-muted underline"
            >
              Xem ảnh chụp màn hình Flow lúc lỗi
            </a>
          </div>
        )}

        <Field label="Bối cảnh quay (tiếng Anh, dùng cho mọi cảnh)" htmlFor="vd-setting">
          <input
            id="vd-setting"
            className={inputClass}
            value={draft.setting}
            disabled={busy}
            onChange={(e) => setDraft({ ...draft, setting: e.target.value })}
          />
        </Field>

        <ol className="mt-3 flex flex-col gap-3">
          {draft.shots.map((shot, i) => (
            <ShotEditor
              key={`${video.id}-${i}`}
              index={i}
              shot={shot}
              state={video.shots[i]}
              language={video.language}
              disabled={busy}
              onChange={(patch) => setShot(i, patch)}
              clipUrl={video.shots[i]?.hasClip ? urls.clip(i) : null}
              onRedo={() => redoShot.mutate(i)}
              redoing={redoShot.isPending && redoShot.variables === i}
            />
          ))}
        </ol>

        {problems.length > 0 && (
          <ul className="mt-3 list-disc pl-5 text-xs text-danger">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}

        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => rewrite.mutate()}
              busy={rewrite.isPending}
              disabled={busy}
            >
              <RefreshCw className="h-3.5 w-3.5" /> AI viết lại
            </Button>
            <Button
              size="sm"
              onClick={() => save.mutate(draft)}
              busy={save.isPending}
              disabled={busy || !dirty || problems.length > 0}
            >
              <Save className="h-3.5 w-3.5" /> Lưu kịch bản
            </Button>
            <Button
              size="sm"
              variant="quiet"
              onClick={() => {
                if (window.confirm("Xóa video này?")) remove.mutate();
              }}
              disabled={busy}
            >
              <Trash2 className="h-3.5 w-3.5" /> Xóa
            </Button>
          </div>
          {video.status !== "ready" || dirty ? (
            <Button
              variant="primary"
              onClick={() => render.mutate()}
              busy={render.isPending}
              // With every clip already made, a failed job can still be re-run to redo the
              // stitching; it spends no credits.
              disabled={
                busy || problems.length > 0 || (remaining === 0 && video.status !== "failed")
              }
              data-testid="VideoDetail.render"
            >
              <Clapperboard className="h-4 w-4" />
              {video.status === "failed" ? "Tạo tiếp bằng Flow" : "Tạo video bằng Google Flow"} ·{" "}
              {video.creditsEstimate} tín dụng
            </Button>
          ) : null}
        </div>
      </Panel>

      {video.hasVideo && (
        <Panel title="5. Video hoàn chỉnh">
          <div className="flex flex-col gap-4 sm:flex-row">
            <video
              key={urls.file}
              controls
              playsInline
              poster={video.hasThumb ? urls.thumb : undefined}
              className="aspect-[9/16] w-full max-w-[320px] rounded-xl bg-black"
              data-testid="VideoDetail.player"
            >
              <source src={urls.file} type="video/mp4" />
              <track
                kind="captions"
                src={urls.captions}
                srcLang={video.language}
                label="Phụ đề"
                default
              />
            </video>
            <div className="flex flex-col gap-2 text-sm">
              <p className="text-studio-muted">
                Thời lượng {formatDuration(video.durationS)} · {video.shotCount} cảnh từ Google Flow
                và thẻ kết có ảnh, tên, giá sản phẩm.
              </p>
              {draft.caption && (
                <p className="rounded-lg bg-studio-card p-3 text-studio-fg">{draft.caption}</p>
              )}
              <a href={urls.file} download className="self-start">
                <Button size="sm" variant="primary">
                  <Download className="h-3.5 w-3.5" /> Tải MP4
                </Button>
              </a>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

function Progress({
  video,
  onCancel,
  cancelling,
}: {
  video: PromoVideo;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const done = video.shots.filter((s) => s.status === "done").length;
  return (
    <div className="mb-3 rounded-xl border border-amber-edge bg-amber-tint p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-2 font-medium text-studio-fg">
          <Spinner /> {video.stage || STATUS_LABEL[video.status]}
        </p>
        <Button size="sm" variant="quiet" onClick={onCancel} busy={cancelling}>
          <Square className="h-3.5 w-3.5" /> Hủy
        </Button>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-studio-card">
        <div
          className="h-full bg-amber transition-all"
          style={{ width: `${Math.round((done / Math.max(video.shotCount, 1)) * 100)}%` }}
        />
      </div>
      <p className="mt-1 text-xs text-studio-muted">
        Mỗi cảnh mất khoảng 2 đến 4 phút trong Flow. Khi gửi từng cảnh, cửa sổ Flow sẽ nổi lên
        khoảng 2 giây để bấm nút gửi rồi trả lại cửa sổ của bạn. Giữ Chrome mở; có thể rời trang
        này.
      </p>
    </div>
  );
}

function ShotEditor({
  index,
  shot,
  state,
  language,
  disabled,
  onChange,
  clipUrl,
  onRedo,
  redoing,
}: {
  index: number;
  shot: PromoShot;
  state: ShotState | undefined;
  language: PromoVideo["language"];
  disabled: boolean;
  onChange: (patch: Partial<PromoShot>) => void;
  clipUrl: string | null;
  onRedo: () => void;
  redoing: boolean;
}) {
  const [open, setOpen] = useState(false);
  const words = wordCount(shot.dialogue);
  const limit = MAX_DIALOGUE_WORDS[language];
  const match = state?.speechMatch ?? null;
  return (
    <li className="rounded-xl border border-studio-border bg-studio-card p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-studio-fg">Cảnh {index + 1}</span>
        <Badge>{BEAT_LABEL[shot.beat]}</Badge>
        {state && (
          <Badge
            tone={
              state.status === "done" ? "live" : state.status === "failed" ? "danger" : "neutral"
            }
          >
            {SHOT_STATUS_LABEL[state.status]}
          </Badge>
        )}
        {match !== null && (
          <Badge tone={match >= SPEECH_MATCH_WARN ? "live" : "danger"}>
            Lời thoại khớp {Math.round(match * 100)}%
          </Badge>
        )}
      </div>
      <div className="flex gap-3">
        <div className="min-w-0 flex-1">
          <Field label={`Lời thoại (${words}/${limit} từ)`} htmlFor={`shot-${index}-dialogue`}>
            <textarea
              id={`shot-${index}-dialogue`}
              rows={2}
              className={inputClass}
              value={shot.dialogue}
              disabled={disabled}
              onChange={(e) => onChange({ dialogue: e.target.value })}
            />
          </Field>
          <button
            type="button"
            className="mt-1 text-xs text-studio-muted underline"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Ẩn chỉ đạo hình ảnh" : "Chỉ đạo hình ảnh (hành động, góc máy, biểu cảm)"}
          </button>
          {open && (
            <div className="mt-2 grid gap-2">
              <Field label="Hành động với sản phẩm (tiếng Anh)" htmlFor={`shot-${index}-action`}>
                <textarea
                  id={`shot-${index}-action`}
                  rows={2}
                  className={inputClass}
                  value={shot.action}
                  disabled={disabled}
                  onChange={(e) => onChange({ action: e.target.value })}
                />
              </Field>
              <div className="grid gap-2 sm:grid-cols-2">
                <Field label="Góc máy" htmlFor={`shot-${index}-camera`}>
                  <input
                    id={`shot-${index}-camera`}
                    className={inputClass}
                    value={shot.camera}
                    disabled={disabled}
                    onChange={(e) => onChange({ camera: e.target.value })}
                  />
                </Field>
                <Field label="Biểu cảm" htmlFor={`shot-${index}-emotion`}>
                  <input
                    id={`shot-${index}-emotion`}
                    className={inputClass}
                    value={shot.emotion}
                    disabled={disabled}
                    onChange={(e) => onChange({ emotion: e.target.value })}
                  />
                </Field>
              </div>
            </div>
          )}
          {state?.transcript && (
            <p className="mt-2 text-xs text-studio-muted">
              <span className="font-medium">Flow đã nói:</span> {state.transcript}
            </p>
          )}
          {state?.error && <p className="mt-2 text-xs text-danger">{state.error}</p>}
        </div>
        {clipUrl && (
          <div className="flex w-24 shrink-0 flex-col items-center gap-1">
            <video
              src={clipUrl}
              className="aspect-[9/16] w-24 rounded-lg bg-black object-cover"
              muted
              loop
              playsInline
              onMouseEnter={(e) => e.currentTarget.play()}
              onMouseLeave={(e) => e.currentTarget.pause()}
            />
            <Button size="sm" variant="quiet" onClick={onRedo} busy={redoing} disabled={disabled}>
              <RotateCcw className="h-3 w-3" /> Tạo lại
            </Button>
          </div>
        )}
      </div>
    </li>
  );
}
