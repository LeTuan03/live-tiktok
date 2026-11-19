"use client";

import { useEffect, useState } from "react";
import type { CustomizeFormValues } from "./CustomizeForm";

const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
const MAX_AVATAR_BYTES = 10 * 1024 * 1024;

const EMPTY_FORM: CustomizeFormValues = {
  voiceId: null,
  voicePublicOwnerId: null,
  script: "",
};

export interface CustomHostForm {
  values: CustomizeFormValues;
  avatarFile: File | null;
  avatarPreviewUrl: string | null;
  avatarError: string | null;
  onChange: <K extends keyof CustomizeFormValues>(key: K, value: CustomizeFormValues[K]) => void;
  onAvatarSelect: (file: File) => void;
  onAvatarClear: () => void;
}

/**
 * Owns the build-form model for /customize: the field values, the chosen avatar
 * file, its preview object-URL, and avatar validation.
 *
 * Lifting this out of the orchestrator is what keeps the form ALIVE across the
 * input ⇄ live mode switch: the values live here, not inside the form view that
 * gets swapped off screen when the host goes live, so "Edit" returns to an
 * untouched form. It is a single-responsibility facade for the form's state and
 * deliberately knows nothing about LiveKit, submission, or which mode is shown.
 */
export function useCustomHostForm(): CustomHostForm {
  const [values, setValues] = useState<CustomizeFormValues>(EMPTY_FORM);
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [avatarPreviewUrl, setAvatarPreviewUrl] = useState<string | null>(null);
  const [avatarError, setAvatarError] = useState<string | null>(null);

  // Manage the avatar preview object URL, revoking the old one on every change.
  useEffect(() => {
    if (!avatarFile) {
      setAvatarPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(avatarFile);
    setAvatarPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [avatarFile]);

  const onAvatarSelect = (file: File) => {
    if (!ACCEPTED_TYPES.includes(file.type)) {
      setAvatarError("That file type won't work. Use JPG, PNG, WebP or GIF.");
      return;
    }
    if (file.size > MAX_AVATAR_BYTES) {
      setAvatarError("That image is over 10MB. Try a smaller file.");
      return;
    }
    setAvatarError(null);
    setAvatarFile(file);
  };

  const onChange = <K extends keyof CustomizeFormValues>(key: K, value: CustomizeFormValues[K]) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  return {
    values,
    avatarFile,
    avatarPreviewUrl,
    avatarError,
    onChange,
    onAvatarSelect,
    onAvatarClear: () => setAvatarFile(null),
  };
}
