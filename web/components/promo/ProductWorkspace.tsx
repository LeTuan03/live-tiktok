"use client";

// Steps 2 and 3 for the selected product: its facts, then "AI viết kịch bản"
// (pick the AI model, the Flow model and the number of shots), plus the list
// of videos already made for it.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Sparkles } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { versionNumbers } from "@/lib/broadcast/logic";
import { apiBaseUrl } from "@/lib/api";
import { createVideo, getFlowStatus, listVideos, videoUrls } from "@/lib/promo/api";
import { STATUS_LABEL, isBusy } from "@/lib/promo/logic";
import { promoKeys } from "@/lib/promo/queryKeys";
import type { CatalogProduct, PromoLanguage, PromoVideo } from "@/lib/promo/types";
import { describeError, listModels } from "@/lib/studio/api";
import { formatPrice, resolveMediaUrl } from "@/lib/studio/logic";
import { studioKeys } from "@/lib/studio/queryKeys";
import { Badge, Button, ErrorNote, Field, inputClass, Panel } from "@/components/studio/ui";

export function ProductWorkspace({
  product,
  selectedVideoId,
  onSelectVideo,
  onEdit,
}: {
  product: CatalogProduct;
  selectedVideoId: string | null;
  onSelectVideo: (id: string | null) => void;
  onEdit: () => void;
}) {
  const image = resolveMediaUrl(product.imageUrl, apiBaseUrl());
  return (
    <div className="flex flex-col gap-4">
      <Panel
        title="2. Thông tin sản phẩm"
        action={
          <Button size="sm" onClick={onEdit}>
            <Pencil className="h-3.5 w-3.5" /> Sửa
          </Button>
        }
      >
        <div className="flex flex-col gap-4 sm:flex-row">
          <div className="h-48 w-full shrink-0 overflow-hidden rounded-xl bg-studio-card sm:w-40">
            {image && (
              <img src={image} alt={product.name} className="h-full w-full object-contain" />
            )}
          </div>
          <div className="min-w-0 flex-1 text-sm">
            <h2 className="text-lg font-bold text-studio-fg">{product.name}</h2>
            <p className="text-studio-muted">
              {[product.brand, product.category, product.sku].filter(Boolean).join(" · ")}
            </p>
            {product.price !== null && (
              <p className="mt-1 text-base font-semibold text-amber">
                {formatPrice(product.price, product.currency)}
              </p>
            )}
            {product.description && <p className="mt-2 text-studio-fg">{product.description}</p>}
            {product.highlights.length > 0 && (
              <ul className="mt-2 list-disc space-y-0.5 pl-5 text-studio-fg">
                {product.highlights.map((h) => (
                  <li key={h}>{h}</li>
                ))}
              </ul>
            )}
            {product.usage && (
              <p className="mt-2 text-studio-muted">
                <span className="font-medium text-studio-fg">Cách dùng:</span> {product.usage}
              </p>
            )}
            {product.talkingPoints && (
              <p className="mt-2 text-studio-muted">
                <span className="font-medium text-studio-fg">Nội dung cần giới thiệu:</span>{" "}
                {product.talkingPoints}
              </p>
            )}
            {product.imageIsPlaceholder && (
              <p className="mt-3 rounded-lg border border-danger/40 bg-danger-tint px-3 py-2 text-danger">
                Sản phẩm này chỉ có ảnh minh họa ngẫu nhiên. Hãy bấm Sửa và tải ảnh chụp sản phẩm
                thật trước khi tạo video, nếu không Flow sẽ dựng sai sản phẩm.
              </p>
            )}
          </div>
        </div>
      </Panel>
      <CreateVideo product={product} onCreated={(v) => onSelectVideo(v.id)} />
      <VideoList product={product} selectedId={selectedVideoId} onSelect={onSelectVideo} />
    </div>
  );
}

function CreateVideo({
  product,
  onCreated,
}: {
  product: CatalogProduct;
  onCreated: (video: PromoVideo) => void;
}) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: studioKeys.models, queryFn: listModels });
  const flow = useQuery({ queryKey: promoKeys.flowStatus, queryFn: getFlowStatus });
  const hosts = (models.data ?? []).filter((m) => m.imageUrl);
  const [hostId, setHostId] = useState("");
  const [flowModel, setFlowModel] = useState("");
  const [shots, setShots] = useState(3);
  const [language, setLanguage] = useState<PromoLanguage>("vi");

  useEffect(() => {
    if (!hostId && hosts.length) {
      setHostId(hosts[0].id);
      setLanguage(hosts[0].language);
    }
  }, [hosts, hostId]);
  useEffect(() => {
    if (!flowModel && flow.data) setFlowModel(flow.data.defaultModel);
  }, [flow.data, flowModel]);

  const create = useMutation({
    mutationFn: () =>
      createVideo({ productId: product.id, modelId: hostId, shots, language, model: flowModel }),
    onSuccess: (video) => {
      qc.invalidateQueries({ queryKey: promoKeys.videos(product.id) });
      qc.setQueryData(promoKeys.video(video.id), video);
      onCreated(video);
    },
  });

  const chosen = flow.data?.models.find((m) => m.label === flowModel);
  const blocked = product.imageIsPlaceholder || !hostId || !flowModel;

  return (
    <Panel title="3. AI viết kịch bản">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Người mẫu AI" htmlFor="cv-host">
          <select
            id="cv-host"
            className={inputClass}
            value={hostId}
            onChange={(e) => {
              setHostId(e.target.value);
              const host = hosts.find((h) => h.id === e.target.value);
              if (host) setLanguage(host.language);
            }}
          >
            {hosts.length === 0 && <option value="">Chưa có người mẫu (tạo ở Studio)</option>}
            {hosts.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model video của Flow" htmlFor="cv-model">
          <select
            id="cv-model"
            className={inputClass}
            value={flowModel}
            onChange={(e) => setFlowModel(e.target.value)}
          >
            {(flow.data?.models ?? []).map((m) => (
              <option key={m.label} value={m.label}>
                {m.label} · {m.credits} tín dụng/cảnh
                {m.retiring ? ` · ngừng ${m.retiring.split("-").reverse().join("/")}` : ""}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Số cảnh (8 giây mỗi cảnh)" htmlFor="cv-shots">
          <select
            id="cv-shots"
            className={inputClass}
            value={shots}
            onChange={(e) => setShots(Number(e.target.value))}
          >
            {[2, 3, 4].map((n) => (
              <option key={n} value={n}>
                {n} cảnh · khoảng {n * 8} giây
              </option>
            ))}
          </select>
        </Field>
        <Field label="Ngôn ngữ lời thoại" htmlFor="cv-lang">
          <select
            id="cv-lang"
            className={inputClass}
            value={language}
            onChange={(e) => setLanguage(e.target.value as PromoLanguage)}
          >
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-studio-muted">
          Viết kịch bản chưa tốn tín dụng. Khi tạo video: khoảng{" "}
          <strong className="text-studio-fg">{(chosen?.credits ?? 0) * shots} tín dụng Flow</strong>
          .
        </p>
        <Button
          variant="primary"
          busy={create.isPending}
          disabled={blocked}
          onClick={() => create.mutate()}
          data-testid="CreateVideo.submit"
        >
          <Sparkles className="h-4 w-4" />{" "}
          {create.isPending ? "AI đang viết..." : "AI viết kịch bản"}
        </Button>
      </div>
      <ErrorNote message={create.isError ? describeError(create.error) : null} />
    </Panel>
  );
}

function VideoList({
  product,
  selectedId,
  onSelect,
}: {
  product: CatalogProduct;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const videos = useQuery({
    queryKey: promoKeys.videos(product.id),
    queryFn: () => listVideos(product.id),
    refetchInterval: (q) => (q.state.data?.some(isBusy) ? 4000 : false),
  });
  const list = videos.data ?? [];
  const versions = useMemo(() => versionNumbers(list), [list]);
  if (list.length === 0) return null;
  return (
    <Panel title={`Video của sản phẩm (${list.length})`}>
      <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {list.map((v) => (
          <li key={v.id}>
            <button
              type="button"
              onClick={() => onSelect(v.id)}
              aria-pressed={v.id === selectedId}
              className={`w-full overflow-hidden rounded-xl border text-left ${
                v.id === selectedId ? "border-amber" : "border-studio-border"
              } bg-studio-card hover:bg-studio-hover`}
            >
              <div className="aspect-[9/16] w-full bg-studio-hover">
                {v.hasThumb && (
                  <img src={videoUrls(v).thumb} alt="" className="h-full w-full object-cover" />
                )}
              </div>
              <div className="p-2">
                <Badge
                  tone={v.status === "ready" ? "live" : v.status === "failed" ? "danger" : "amber"}
                >
                  {STATUS_LABEL[v.status]}
                </Badge>
                <p className="mt-1 truncate text-[11px] font-semibold text-studio-fg">
                  Bản {versions.get(v.id) ?? 1}
                  {v.durationS ? ` · ${Math.round(v.durationS)}s` : ""}
                </p>
                <p className="truncate text-[11px] text-studio-muted">
                  {v.hostName} · {v.flowModel}
                </p>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
