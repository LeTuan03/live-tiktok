"use client";

import { useId, useRef, useState } from "react";
import type { DragEvent } from "react";
import { ImageUp, Pencil, X } from "lucide-react";

const ACCEPTED = "image/jpeg,image/png,image/webp,image/gif";

interface AvatarDropzoneProps {
  previewUrl: string | null;
  onSelect: (file: File) => void;
  onClear: () => void;
  error?: string | null;
}

/**
 * 336×608 avatar picker — the hero control. Click or drag-drop an image; once
 * set, the portrait fills the 336×608 frame with a remove affordance. The
 * uploaded image is what LemonSlice renders, so the frame previews the exact
 * size the avatar will use.
 */
export function AvatarDropzone({ previewUrl, onSelect, onClear, error }: AvatarDropzoneProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const hintId = useId();

  const pick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) onSelect(file);
  };

  const onDrop = (e: DragEvent<HTMLButtonElement>) => {
    e.preventDefault();
    setDragOver(false);
    pick(e.dataTransfer.files);
  };

  const base =
    "relative mx-auto flex aspect-[336/608] w-full max-w-[336px] flex-col items-center justify-center gap-3 overflow-hidden rounded-xl border-2 border-dashed bg-studio-card text-center transition-[colors,box-shadow] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber focus-visible:ring-offset-2 focus-visible:ring-offset-studio-bg";
  const state = error
    ? "border-danger"
    : dragOver
      ? "border-amber bg-amber-tint"
      : "border-studio-border hover:border-studio-edge";

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="sr-only"
        aria-describedby={hintId}
        onChange={(e) => {
          pick(e.currentTarget.files);
          e.currentTarget.value = "";
        }}
      />
      {previewUrl ? (
        <div className="relative mx-auto aspect-[336/608] w-full max-w-[336px] overflow-hidden rounded-xl ring-1 ring-studio-border">
          <img src={previewUrl} alt="" className="h-full w-full object-cover object-center" />
          <button
            type="button"
            onClick={onClear}
            aria-label="Remove image"
            className="absolute right-2 top-2 flex h-8 w-8 items-center justify-center rounded-full bg-black/55 text-studio-fg ring-1 ring-white/15 backdrop-blur transition-[colors,box-shadow] duration-150 hover:bg-black/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber focus-visible:ring-offset-2 focus-visible:ring-offset-studio-bg"
          >
            <X size={16} strokeWidth={2.2} />
          </button>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="absolute inset-x-0 bottom-2 mx-auto flex w-fit items-center gap-1.5 rounded-full bg-black/55 px-3 py-1.5 text-[12.5px] font-medium text-studio-fg backdrop-blur transition-[colors,box-shadow] duration-150 hover:bg-black/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber focus-visible:ring-offset-2 focus-visible:ring-offset-studio-bg"
          >
            <Pencil size={13} strokeWidth={2} />
            Edit
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          className={`${base} ${state} cursor-pointer`}
        >
          <span className="flex h-12 w-12 items-center justify-center text-studio-faint">
            <ImageUp size={28} strokeWidth={1.75} />
          </span>
          <span className="px-4 text-sm font-medium text-studio-muted">Add a portrait</span>
          <span className="px-4 text-xs text-studio-faint">Drag an image here, or browse</span>
        </button>
      )}
      <p
        id={hintId}
        className={`mt-2 text-[12.5px] ${error ? "text-danger" : "text-studio-faint"}`}
      >
        {error ?? "Vertical 336×608. JPG, PNG, WebP or GIF, up to 10MB."}
      </p>
    </div>
  );
}
