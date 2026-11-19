"use client";

import { useEffect, useReducer, useRef, useState } from "react";
import { createCustomHost } from "@/lib/api/customize";
import { INITIAL_EXPERIENCE, experienceReducer } from "@/lib/customize/experienceMachine";
import { CustomHostVideo } from "./CustomHostVideo";
import { CustomizeForm } from "./CustomizeForm";
import { useCustomHostForm } from "./useCustomHostForm";
import { useCustomHostRoom } from "./useCustomHostRoom";

// Personality is no longer collected from the user: the host just reads the
// script, so every custom host gets the same generic, friendly delivery persona.
const DEFAULT_PERSONALITY =
  "Friendly and upbeat. Reads the script naturally, like a real person talking to a friend, with clear, warm energy.";

/**
 * Orchestrates the /customize page. It wires three single-purpose collaborators
 * and owns nothing else:
 *   - useCustomHostForm  — the build-form model (values + avatar), kept alive
 *                          across the mode switch so "Edit" returns untouched.
 *   - experienceReducer  — a finite-state machine over the two visible modes
 *                          (input ⇄ live, via a transient "submitting"); the
 *                          single authority on which transitions are legal.
 *   - useCustomHostRoom  — the LiveKit room lifecycle, connected only while a
 *                          live session exists and torn down the moment we edit.
 *
 * The view is a straight switch on the machine's status: the build form in
 * input mode, the real-time monitor in live mode. There is deliberately no
 * preview monitor in input mode, so each mode shows exactly one surface.
 */
export function CustomizeExperience() {
  const form = useCustomHostForm();
  const [state, dispatch] = useReducer(experienceReducer, INITIAL_EXPERIENCE);
  const [muted, setMuted] = useState(false);

  // Guards the one-shot "viewer is ready, deliver the pitch" signal so it fires
  // exactly once per live session (on connect), not on every re-render.
  const readySignaledRef = useRef(false);

  const live = state.status === "live";
  const session = live ? state.session : null;

  const { videoTrack, audioTrack, phase, canPlaybackAudio, startAudio, sendViewerReady } =
    useCustomHostRoom(session?.livekitUrl ?? null, session?.token ?? null);

  // As soon as the room is connected, unlock audio playback and tell the agent
  // the viewer is ready so it delivers the pitch right after the avatar video
  // starts streaming. `sendViewerReady` retries until the agent's audio track
  // arrives, so a packet that races the agent's handler isn't lost. If the
  // browser blocks autoplay, the in-player "tap to unmute" affordance recovers.
  useEffect(() => {
    if (!live) {
      readySignaledRef.current = false;
      return;
    }
    if (phase === "connected" && !readySignaledRef.current) {
      readySignaledRef.current = true;
      void startAudio();
      void sendViewerReady();
    }
  }, [live, phase]);

  const onSubmit = async () => {
    if (!form.avatarFile || !form.values.voiceId) return;
    dispatch({ type: "submit" });
    setMuted(false);
    try {
      const created = await createCustomHost({
        avatar: form.avatarFile,
        voiceId: form.values.voiceId,
        publicOwnerId: form.values.voicePublicOwnerId,
        personality: DEFAULT_PERSONALITY,
        script: form.values.script.trim(),
      });
      dispatch({ type: "submitSucceeded", session: created });
    } catch (err) {
      dispatch({
        type: "submitFailed",
        error: err instanceof Error ? err.message : "That didn't go through. Try again.",
      });
    }
  };

  // Back to the build form. The machine resets to input mode; the form model is
  // a separate owner, so every field is exactly as the user left it, ready to
  // tweak and put on air again.
  const onEdit = () => {
    dispatch({ type: "edit" });
    setMuted(false);
  };

  const onToggleMute = () => {
    setMuted((prev) => {
      const next = !prev;
      if (!next) void startAudio();
      return next;
    });
  };

  const submitting = state.status === "submitting";
  const submitError = state.status === "input" ? state.error : null;

  return (
    <div className="relative min-h-[100dvh] bg-studio-bg">
      {/* On-air rail: a thin green line across the very top, only while live. */}
      <div
        aria-hidden
        className={`pointer-events-none fixed inset-x-0 top-0 z-50 h-px bg-live transition-opacity duration-300 ${
          live ? "opacity-80" : "opacity-0"
        }`}
      />

      {live ? (
        // LIVE MODE. The real-time monitor takes over: fullscreen on mobile, a
        // centered 9:16 frame on desktop. "Edit" (top-left of the monitor)
        // returns to input mode with every field preserved.
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-studio-bg lg:p-6">
          <div className="animate-stage-in relative mx-auto h-full min-h-0 w-full min-w-0 lg:aspect-[9/16] lg:h-full lg:w-auto lg:max-h-full lg:max-w-full">
            <CustomHostVideo
              videoTrack={videoTrack}
              audioTrack={audioTrack}
              placeholderUrl={form.avatarPreviewUrl}
              phase={phase}
              muted={muted}
              canPlaybackAudio={canPlaybackAudio}
              onToggleMute={onToggleMute}
              onReset={onEdit}
            />
          </div>
        </div>
      ) : (
        // INPUT MODE. Just the build form, centered in a single column. No live
        // preview monitor here; the avatar dropzone already previews the face.
        <div className="animate-stage-in mx-auto w-full max-w-lg px-4 py-8 lg:py-12">
          <div className="rounded-2xl border border-studio-border bg-studio-panel p-5 shadow-[inset_0_1px_0_rgba(255,255,255,0.03),0_16px_40px_-24px_rgba(0,0,0,0.85)] lg:p-6">
            <CustomizeForm
              values={form.values}
              avatarPreviewUrl={form.avatarPreviewUrl}
              avatarError={form.avatarError}
              submitting={submitting}
              submitError={submitError}
              onChange={form.onChange}
              onAvatarSelect={form.onAvatarSelect}
              onAvatarClear={form.onAvatarClear}
              onSubmit={onSubmit}
            />
          </div>
        </div>
      )}
    </div>
  );
}
