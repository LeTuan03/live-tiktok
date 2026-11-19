"use client";

// "Người mẫu AI": list, create, edit, delete models and build their motion
// avatar. Polls while any avatar build is in progress.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import {
  buildModelAvatar,
  createModel,
  deleteModel,
  describeError,
  listModels,
  listVoices,
  updateModel,
} from "@/lib/studio/api";
import { resolveMediaUrl } from "@/lib/studio/logic";
import { studioKeys } from "@/lib/studio/queryKeys";
import type { StudioLanguage, StudioModel, StudioVoice } from "@/lib/studio/types";
import { useToast } from "@/lib/ui/toast";
import { Badge, Button, ErrorNote, Field, inputClass, Panel } from "./ui";

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const LANGUAGE_LABEL: Record<StudioLanguage, string> = { vi: "Tiếng Việt", en: "English" };

export function ModelsSection() {
  const qc = useQueryClient();
  const toast = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<StudioModel | null>(null);

  const models = useQuery({
    queryKey: studioKeys.models,
    queryFn: listModels,
    staleTime: 0,
    refetchInterval: (query) =>
      query.state.data?.some((m) => m.avatarStatus === "processing") ? 2000 : false,
  });
  const voices = useQuery({ queryKey: studioKeys.voices, queryFn: listVoices, staleTime: 60_000 });

  const replaceModel = (model: StudioModel) => {
    qc.setQueryData<StudioModel[]>(studioKeys.models, (prev) =>
      prev ? prev.map((m) => (m.id === model.id ? model : m)) : [model],
    );
  };

  const build = useMutation({
    mutationFn: buildModelAvatar,
    onSuccess: replaceModel,
    onError: (err) =>
      toast.show({ kind: "error", title: "Không tạo được avatar", body: describeError(err) }),
  });

  const remove = useMutation({
    mutationFn: deleteModel,
    onSuccess: () => qc.invalidateQueries({ queryKey: studioKeys.models }),
    onError: (err) =>
      toast.show({ kind: "error", title: "Không xóa được người mẫu", body: describeError(err) }),
  });

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
  };

  return (
    <Panel
      title="Người mẫu AI"
      action={
        !formOpen && (
          <Button
            variant="primary"
            size="sm"
            data-testid="Models.new"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus className="h-3.5 w-3.5" /> Tạo người mẫu
          </Button>
        )
      }
    >
      {formOpen && (
        <ModelForm
          key={editing?.id ?? "new"}
          model={editing}
          voices={voices.data ?? []}
          voicesError={voices.isError ? describeError(voices.error) : null}
          onCancel={closeForm}
          onSaved={(model) => {
            if (editing) replaceModel(model);
            void qc.invalidateQueries({ queryKey: studioKeys.models });
            void qc.invalidateQueries({ queryKey: studioKeys.sessions });
            closeForm();
          }}
        />
      )}

      {models.isLoading && <p className="text-sm text-studio-faint">Đang tải người mẫu...</p>}
      {models.isError && <ErrorNote message={describeError(models.error)} />}
      {models.data && models.data.length === 0 && !formOpen && (
        <p className="text-sm text-studio-faint">
          Chưa có người mẫu nào. Tạo một người mẫu từ ảnh chân dung để bắt đầu.
        </p>
      )}

      <ul className="mt-3 grid gap-3 sm:grid-cols-2">
        {(models.data ?? []).map((model) => (
          <ModelCard
            key={model.id}
            model={model}
            voiceLabel={voices.data?.find((v) => v.id === model.voice)?.label ?? model.voice}
            building={build.isPending && build.variables === model.id}
            deleting={remove.isPending && remove.variables === model.id}
            onBuild={() => build.mutate(model.id)}
            onEdit={() => {
              setEditing(model);
              setFormOpen(true);
            }}
            onDelete={() => {
              if (window.confirm(`Xóa người mẫu "${model.name}"?`)) remove.mutate(model.id);
            }}
          />
        ))}
      </ul>
    </Panel>
  );
}

function AvatarStatusLine({ model }: { model: StudioModel }) {
  if (model.avatarStatus === "ready") return <Badge tone="live">Avatar chuyển động sẵn sàng</Badge>;
  if (model.avatarStatus === "processing") {
    const pct = Math.round(Math.min(1, Math.max(0, model.avatarProgress ?? 0)) * 100);
    return (
      <div className="flex flex-col gap-1" data-testid="Models.avatarProgress">
        <Badge tone="amber">Đang tạo avatar {pct}%</Badge>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-studio-card">
          <div className="h-full bg-amber transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }
  if (model.avatarStatus === "failed") {
    return (
      <div className="flex flex-col gap-1">
        <Badge tone="danger">Tạo avatar thất bại</Badge>
        {model.avatarError && (
          <p className="break-words text-xs text-danger">{model.avatarError}</p>
        )}
      </div>
    );
  }
  return <Badge>Chưa có avatar chuyển động</Badge>;
}

function ModelCard({
  model,
  voiceLabel,
  building,
  deleting,
  onBuild,
  onEdit,
  onDelete,
}: {
  model: StudioModel;
  voiceLabel: string;
  building: boolean;
  deleting: boolean;
  onBuild: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const image = resolveMediaUrl(model.imageUrl, apiBaseUrl());
  const processing = model.avatarStatus === "processing";
  return (
    <li
      data-testid="Models.card"
      className="flex gap-3 rounded-xl border border-studio-border bg-studio-card p-3"
    >
      <div className="h-28 w-20 shrink-0 overflow-hidden rounded-lg bg-studio-hover">
        {image && <img src={image} alt={model.name} className="h-full w-full object-cover" />}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div>
          <p className="truncate text-sm font-semibold text-studio-fg">{model.name}</p>
          <p className="truncate text-xs text-studio-muted">
            {LANGUAGE_LABEL[model.language]}
            {voiceLabel ? ` · ${voiceLabel}` : ""}
          </p>
        </div>
        <AvatarStatusLine model={model} />
        <div className="mt-auto flex flex-wrap gap-1.5">
          <Button size="sm" onClick={onBuild} busy={building} disabled={processing}>
            <RefreshCw className="h-3.5 w-3.5" />
            {model.avatarStatus === "none" ? "Tạo avatar chuyển động" : "Tạo lại avatar"}
          </Button>
          <Button size="sm" variant="quiet" onClick={onEdit} aria-label={`Sửa ${model.name}`}>
            <Pencil className="h-3.5 w-3.5" /> Sửa
          </Button>
          <Button
            size="sm"
            variant="quiet"
            onClick={onDelete}
            busy={deleting}
            aria-label={`Xóa ${model.name}`}
          >
            <Trash2 className="h-3.5 w-3.5" /> Xóa
          </Button>
        </div>
      </div>
    </li>
  );
}

function ModelForm({
  model,
  voices,
  voicesError,
  onCancel,
  onSaved,
}: {
  model: StudioModel | null;
  voices: StudioVoice[];
  voicesError: string | null;
  onCancel: () => void;
  onSaved: (model: StudioModel) => void;
}) {
  const [name, setName] = useState(model?.name ?? "");
  const [intro, setIntro] = useState(model?.intro ?? "");
  const [personality, setPersonality] = useState(model?.personality ?? "");
  const [language, setLanguage] = useState<StudioLanguage>(model?.language ?? "vi");
  const [voice, setVoice] = useState(model?.voice ?? "");
  const [image, setImage] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const languageVoices = useMemo(
    () => voices.filter((v) => v.language.toLowerCase().startsWith(language)),
    [voices, language],
  );

  // Pick a default voice once the list loads. A stored voice is never
  // replaced silently, even when it belongs to another language; the select
  // below shows it as its own option so the user decides.
  useEffect(() => {
    if (!voice && languageVoices.length > 0) setVoice(languageVoices[0].id);
  }, [languageVoices, voice]);

  const changeLanguage = (next: StudioLanguage) => {
    setLanguage(next);
    const first = voices.find((v) => v.language.toLowerCase().startsWith(next));
    setVoice(first?.id ?? "");
  };
  const voiceMismatch = voice !== "" && !languageVoices.some((v) => v.id === voice);

  const preview = useMemo(() => (image ? URL.createObjectURL(image) : null), [image]);
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview);
    };
  }, [preview]);

  const save = useMutation({
    mutationFn: async () => {
      if (model) {
        return updateModel(model.id, { name: name.trim(), intro, personality, language, voice });
      }
      if (!image) throw new Error("Hãy chọn ảnh chân dung cho người mẫu.");
      return createModel({ name: name.trim(), intro, personality, language, voice, image });
    },
    onSuccess: onSaved,
    onError: (err) => setError(describeError(err)),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("Hãy nhập tên người mẫu.");
      return;
    }
    if (!model && !image) {
      setError("Hãy chọn ảnh chân dung cho người mẫu.");
      return;
    }
    save.mutate();
  };

  return (
    <form
      onSubmit={submit}
      data-testid="Models.form"
      className="mb-4 grid gap-3 rounded-xl border border-studio-edge bg-studio-bg p-4 sm:grid-cols-2"
    >
      <Field label="Tên người mẫu" htmlFor="model-name">
        <input
          id="model-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={80}
          className={inputClass}
          placeholder="Ví dụ: Linh"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Ngôn ngữ" htmlFor="model-language">
          <select
            id="model-language"
            value={language}
            onChange={(e) => changeLanguage(e.target.value as StudioLanguage)}
            className={inputClass}
          >
            <option value="vi">Tiếng Việt</option>
            <option value="en">English</option>
          </select>
        </Field>
        <Field label="Giọng nói" htmlFor="model-voice">
          <select
            id="model-voice"
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
            className={inputClass}
            disabled={languageVoices.length === 0 && !voiceMismatch}
          >
            {languageVoices.length === 0 && !voiceMismatch && <option value="">Mặc định</option>}
            {voiceMismatch && (
              <option value={voice}>
                {voices.find((v) => v.id === voice)?.label ?? voice} (khác ngôn ngữ)
              </option>
            )}
            {languageVoices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field
        label="Giới thiệu bản thân"
        htmlFor="model-intro"
        hint="Người mẫu dùng thông tin này khi tự giới thiệu và trò chuyện."
      >
        <textarea
          id="model-intro"
          value={intro}
          onChange={(e) => setIntro(e.target.value)}
          rows={3}
          className={inputClass}
          placeholder="Ví dụ: Mình là Linh, 25 tuổi, mê skincare và đã bán hàng online 3 năm."
        />
      </Field>
      <Field label="Tính cách, phong cách nói" htmlFor="model-personality">
        <textarea
          id="model-personality"
          value={personality}
          onChange={(e) => setPersonality(e.target.value)}
          rows={3}
          className={inputClass}
          placeholder="Ví dụ: Vui vẻ, gần gũi, hay đùa, nói chuyện như bạn bè."
        />
      </Field>
      {!model && (
        <div className="sm:col-span-2">
          <Field
            label="Ảnh chân dung"
            htmlFor="model-image"
            hint="JPG, PNG hoặc WebP, tối đa 8 MB. Ảnh dọc, thấy rõ khuôn mặt chính diện cho kết quả tốt nhất."
          >
            <div className="flex items-center gap-3">
              {preview && (
                <img src={preview} alt="Xem trước" className="h-24 w-16 rounded-lg object-cover" />
              )}
              <input
                id="model-image"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={(e) => {
                  const file = e.target.files?.[0] ?? null;
                  if (file && file.size > MAX_IMAGE_BYTES) {
                    setError("Ảnh lớn hơn 8 MB. Hãy chọn ảnh nhỏ hơn.");
                    e.target.value = "";
                    setImage(null);
                    return;
                  }
                  setError(null);
                  setImage(file);
                }}
                className="text-sm text-studio-muted file:mr-3 file:rounded-lg file:border-0 file:bg-studio-hover file:px-3 file:py-2 file:text-sm file:text-studio-fg"
              />
            </div>
          </Field>
        </div>
      )}
      <div className="flex flex-col gap-2 sm:col-span-2">
        <ErrorNote message={error ?? voicesError} />
        <div className="flex justify-end gap-2">
          <Button variant="quiet" onClick={onCancel} disabled={save.isPending}>
            Hủy
          </Button>
          <Button type="submit" variant="primary" busy={save.isPending} data-testid="Models.save">
            {model ? "Lưu thay đổi" : "Tạo người mẫu"}
          </Button>
        </div>
      </div>
    </form>
  );
}
