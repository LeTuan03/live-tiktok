"use client";

import { AlertCircle, Check, Loader2 } from "lucide-react";
import {
  WORD_COUNT_MAX,
  WORD_COUNT_MIN,
  countWords,
  wordBandState,
} from "@/lib/customize/wordCount";
import { AvatarDropzone } from "./AvatarDropzone";
import { VoicePicker } from "./VoicePicker";

const CONTROL =
  "w-full rounded-xl border border-studio-border bg-studio-card px-4 py-3 text-[15px] text-studio-fg placeholder:text-studio-faint transition-[border-color,box-shadow] duration-150 hover:border-studio-edge focus:border-amber focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber focus-visible:ring-offset-2 focus-visible:ring-offset-studio-bg";

export interface CustomizeFormValues {
  voiceId: string | null;
  voicePublicOwnerId: string | null;
  script: string;
}

interface CustomizeFormProps {
  values: CustomizeFormValues;
  avatarPreviewUrl: string | null;
  avatarError: string | null;
  submitting: boolean;
  submitError: string | null;
  onChange: <K extends keyof CustomizeFormValues>(key: K, value: CustomizeFormValues[K]) => void;
  onAvatarSelect: (file: File) => void;
  onAvatarClear: () => void;
  onSubmit: () => void;
}

function Section({
  label,
  hint,
  done,
  children,
}: {
  label: string;
  hint?: string;
  done?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-studio-muted">
          {label}
          {done && <Check size={14} className="text-live" aria-label="ready" />}
        </span>
        {hint && <span className="text-[12.5px] text-studio-faint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function CustomizeForm({
  values,
  avatarPreviewUrl,
  avatarError,
  submitting,
  submitError,
  onChange,
  onAvatarSelect,
  onAvatarClear,
  onSubmit,
}: CustomizeFormProps) {
  const wordCount = countWords(values.script);
  const band = wordBandState(wordCount);
  const counterColor =
    band === "ok" ? "text-live" : band === "high" ? "text-danger" : "text-studio-faint";

  const hasAvatar = !!avatarPreviewUrl;
  const hasVoice = !!values.voiceId;
  const pitchOk = band === "ok";
  const ready = hasAvatar && hasVoice && pitchOk;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (ready && !submitting) onSubmit();
      }}
      className="flex flex-col gap-6"
    >
      <header className="space-y-1">
        <h1 className="text-[28px] font-semibold leading-[1.1] tracking-[-0.02em] text-studio-fg lg:text-[30px]">
          Build your host
        </h1>
        <p className="text-sm text-studio-muted">
          Cast a face, write a pitch, pick a voice. Then put them on air.
        </p>
      </header>

      <Section label="Face" done={hasAvatar}>
        <AvatarDropzone
          previewUrl={avatarPreviewUrl}
          onSelect={onAvatarSelect}
          onClear={onAvatarClear}
          error={avatarError}
        />
      </Section>

      <hr className="border-studio-border" />

      <Section label="Pitch" hint="30-45 words, about 15 seconds" done={pitchOk}>
        <textarea
          value={values.script}
          rows={4}
          placeholder="Open with a hook, name the product and why it matters, end with a clear call to tap and shop."
          onChange={(e) => onChange("script", e.target.value)}
          className={`${CONTROL} min-h-[120px] resize-y leading-relaxed`}
        />
        <div className={`text-right font-mono text-[12px] tabular-nums ${counterColor}`}>
          {wordCount}/{WORD_COUNT_MAX}
        </div>
      </Section>

      <hr className="border-studio-border" />

      <Section label="Voice" hint="Preview, then choose one" done={hasVoice}>
        <VoicePicker
          selectedVoiceId={values.voiceId}
          onSelect={(id, ownerId) => {
            onChange("voiceId", id);
            onChange("voicePublicOwnerId", ownerId);
          }}
        />
      </Section>

      {submitError && (
        <p className="flex items-center gap-2 rounded-xl border border-danger/40 bg-danger-tint px-4 py-3 text-sm text-danger">
          <AlertCircle size={18} className="shrink-0" />
          {submitError || "That didn't go through. Try again."}
        </p>
      )}

      <div className="space-y-2">
        <button
          type="submit"
          disabled={!ready || submitting}
          className={`flex h-12 w-full items-center justify-center gap-2 rounded-xl text-base font-semibold transition-colors duration-200 active:translate-y-px ${
            ready || submitting
              ? "bg-live text-live-fg shadow-[0_8px_24px_-12px_rgba(36,255,80,0.5)] hover:bg-live-hover"
              : "cursor-not-allowed border border-studio-border bg-studio-card text-studio-disabled"
          }`}
        >
          {submitting ? (
            <>
              <Loader2 size={18} className="animate-spin" />
              Going live…
            </>
          ) : (
            "Put on air"
          )}
        </button>
        {!ready && !submitting && (
          <p className="text-center text-[12.5px] text-studio-muted">
            Add a face, a {WORD_COUNT_MIN}-{WORD_COUNT_MAX} word pitch, and a voice to go on air.
          </p>
        )}
      </div>
    </form>
  );
}
