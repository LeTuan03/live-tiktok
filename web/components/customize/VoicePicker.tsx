"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Pause, Play, User } from "lucide-react";
import { ApiError } from "@/lib/api";
import { type CustomizeVoice, listVoices } from "@/lib/api/customize";

interface VoicePickerProps {
  selectedVoiceId: string | null;
  onSelect: (voiceId: string, publicOwnerId: string | null) => void;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 503) {
    return "Voices are unavailable right now. Check the server's ElevenLabs key.";
  }
  return err instanceof Error ? err.message : "Couldn't load voices. Try again.";
}

function VoiceAvatar({ voice, selected }: { voice: CustomizeVoice; selected: boolean }) {
  const ring = selected ? "ring-amber" : "ring-studio-border";
  if (voice.imageUrl) {
    return (
      <img
        src={voice.imageUrl}
        alt=""
        className={`h-9 w-9 flex-shrink-0 rounded-full object-cover ring-1 ${ring}`}
      />
    );
  }
  return (
    <span
      className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-studio-hover text-studio-faint ring-1 ${ring}`}
    >
      <User size={16} />
    </span>
  );
}

/**
 * Curated voice picker. Fetches the server's short, hand-picked list once on
 * mount and renders it as two labeled groups, Male and Female. Each voice is a
 * selectable card with its name, a one-line "vibe", and an optional audio
 * preview toggle. No search, filters, or pagination.
 */
export function VoicePicker({ selectedVoiceId, onSelect }: VoicePickerProps) {
  const [voices, setVoices] = useState<CustomizeVoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);

  // Load the curated list once on mount.
  useEffect(() => {
    const ac = new AbortController();
    setLoading(true);
    setError(null);

    listVoices(ac.signal)
      .then((list) => {
        if (ac.signal.aborted) return;
        setVoices(list);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === "AbortError") return;
        setError(errorMessage(err));
        setVoices([]);
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });

    return () => ac.abort();
  }, []);

  // Stop playback on unmount.
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      audioRef.current = null;
    };
  }, []);

  const togglePreview = (voice: CustomizeVoice) => {
    if (!voice.previewUrl) return;
    if (playingId === voice.voiceId) {
      audioRef.current?.pause();
      setPlayingId(null);
      return;
    }
    audioRef.current?.pause();
    const audio = new Audio(voice.previewUrl);
    audioRef.current = audio;
    audio.onended = () => setPlayingId((cur) => (cur === voice.voiceId ? null : cur));
    void audio.play().catch(() => setPlayingId(null));
    setPlayingId(voice.voiceId);
  };

  const select = (voice: CustomizeVoice) => {
    onSelect(voice.voiceId, voice.publicOwnerId);
  };

  if (error) {
    return (
      <p className="rounded-xl border border-danger/40 bg-danger-tint px-4 py-3 text-sm text-danger">
        {error}
      </p>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-studio-border bg-studio-card px-4 py-6 text-sm text-studio-muted">
        <Loader2 size={16} className="animate-spin" />
        Loading voices…
      </div>
    );
  }

  const male = voices.filter((v) => v.gender === "male");
  const female = voices.filter((v) => v.gender === "female");

  const renderCard = (voice: CustomizeVoice) => {
    const selected = voice.voiceId === selectedVoiceId;
    const playing = playingId === voice.voiceId;
    return (
      // biome-ignore lint/a11y/useSemanticElements: the card wraps a nested play <button>, so it must be a div with ARIA radio role, not an <input type=radio>
      <div
        key={voice.voiceId}
        role="radio"
        aria-checked={selected}
        tabIndex={0}
        onClick={() => select(voice)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            select(voice);
          }
        }}
        className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl border p-3 transition-[colors] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber focus-visible:ring-offset-2 focus-visible:ring-offset-studio-bg ${
          selected
            ? "border-amber bg-amber-tint"
            : "border-transparent bg-studio-card hover:bg-studio-hover"
        }`}
      >
        <div className="flex min-w-0 items-center gap-3">
          <VoiceAvatar voice={voice} selected={selected} />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-studio-fg">{voice.name}</div>
            {voice.descriptive && (
              <div className="truncate text-[13px] text-studio-faint">{voice.descriptive}</div>
            )}
          </div>
        </div>
        {voice.previewUrl && (
          <button
            type="button"
            aria-label={playing ? `Stop ${voice.name} preview` : `Play ${voice.name} preview`}
            onClick={(e) => {
              e.stopPropagation();
              togglePreview(voice);
            }}
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full text-studio-faint transition-[colors] duration-150 hover:text-studio-fg active:scale-95"
          >
            {playing ? <Pause size={15} /> : <Play size={15} />}
          </button>
        )}
      </div>
    );
  };

  const renderGroup = (label: string, group: CustomizeVoice[]) => {
    if (group.length === 0) return null;
    return (
      <div className="space-y-2">
        <h3 className="text-[13px] font-medium text-studio-muted">{label}</h3>
        <div role="radiogroup" aria-label={label} className="space-y-2">
          {group.map(renderCard)}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {renderGroup("Male", male)}
      {renderGroup("Female", female)}
    </div>
  );
}
