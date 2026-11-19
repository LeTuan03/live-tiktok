"use client";

// Plays a broadcast's timeline with two stacked <video> elements.
//
// The visible element plays the current slot while the hidden one buffers
// the next, so a transition swaps two ready elements (a short crossfade)
// instead of loading a file. Everything is derived from the playhead: on
// every tick the player locates the on-air position, re-seeks the visible
// video only when it drifts more than a second from the schedule, and swaps
// layers when the slot changes. A timer set to the end of the current slot
// makes the swap land on time rather than on the next tick.

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import type { BroadcastItem } from "@/lib/broadcast/types";
import { DRIFT_TOLERANCE_S, drift, type LayerPlan, planPlayer } from "@/lib/broadcast/engine";
import { locate, type Playhead, remainingMs, type Timeline } from "@/lib/broadcast/timeline";

export interface BroadcastPlayerProps {
  timeline: Timeline;
  items: Map<string, BroadcastItem>;
  playhead: Playhead | null;
  /** Server clock minus browser clock. */
  clockOffsetMs: number;
  muted: boolean;
  /** The browser refused to play with sound; the player fell back to muted. */
  onSoundBlocked?: () => void;
  fit?: "contain" | "cover";
  className?: string;
  /** Overlays drawn above the video. */
  children?: ReactNode;
}

const TICK_MS = 250;

export function BroadcastPlayer({
  timeline,
  items,
  playhead,
  clockOffsetMs,
  muted,
  onSoundBlocked,
  fit = "contain",
  className = "",
  children,
}: BroadcastPlayerProps) {
  const refA = useRef<HTMLVideoElement>(null);
  const refB = useRef<HTMLVideoElement>(null);
  const layers = useRef<(LayerPlan | null)[]>([null, null]);
  const activeRef = useRef(0);
  const boundary = useRef<number | undefined>(undefined);
  const [active, setActive] = useState(0);
  const [showing, setShowing] = useState(false);
  const latest = useRef({ timeline, items, playhead, clockOffsetMs, muted, onSoundBlocked });
  latest.current = { timeline, items, playhead, clockOffsetMs, muted, onSoundBlocked };

  // Set when the browser refused sound; cleared when the user unmutes again
  // (that click is the gesture the browser wanted).
  const soundBlocked = useRef(false);
  useEffect(() => {
    if (!muted) soundBlocked.current = false;
  }, [muted]);

  const play = useCallback((video: HTMLVideoElement) => {
    video.play().catch((err: unknown) => {
      if ((err as { name?: string })?.name === "NotAllowedError" && !video.muted) {
        soundBlocked.current = true;
        video.muted = true;
        latest.current.onSoundBlocked?.();
        video.play().catch(() => {});
      }
    });
  }, []);

  const load = useCallback((video: HTMLVideoElement, index: number, plan: LayerPlan) => {
    layers.current[index] = plan;
    if (video.dataset.src !== plan.src) {
      video.dataset.src = plan.src;
      video.src = plan.src;
      video.load();
    }
    video.loop = plan.loop;
    video.muted = true;
    video.pause();
    try {
      video.currentTime = plan.timeS;
    } catch {
      // Not seekable yet; the swap seeks again once it can play.
    }
  }, []);

  const sync = useCallback(
    (video: HTMLVideoElement, plan: LayerPlan, playing: boolean, muted: boolean) => {
      video.loop = plan.loop;
      video.muted = muted;
      const off = drift(video.currentTime, plan.timeS, plan.loop ? plan.videoS : null);
      if (playing) {
        if (video.readyState >= 1 && off > DRIFT_TOLERANCE_S) video.currentTime = plan.timeS;
        if (video.paused && !video.ended) play(video);
      } else {
        if (!video.paused) video.pause();
        if (video.readyState >= 1 && off > 0.25) video.currentTime = plan.timeS;
      }
    },
    [play],
  );

  const tick = useCallback(() => {
    const { timeline, items, playhead, clockOffsetMs } = latest.current;
    const muted = latest.current.muted || soundBlocked.current;
    const a = activeRef.current;
    const b = 1 - a;
    const videos = [refA.current, refB.current];
    const va = videos[a];
    const vb = videos[b];
    if (!va || !vb) return;
    window.clearTimeout(boundary.current);
    const position = playhead ? locate(timeline, playhead, Date.now() + clockOffsetMs) : null;
    const plan = planPlayer(timeline, items, position, !!playhead?.playing);
    if (!plan.show || !position) {
      va.pause();
      vb.pause();
      setShowing(false);
      return;
    }
    setShowing(true);
    if (plan.playing) {
      boundary.current = window.setTimeout(tick, remainingMs(position) + 15);
    }
    if (layers.current[a]?.key === plan.show.key) {
      sync(va, plan.show, plan.playing, muted);
      if (plan.preload && layers.current[b]?.key !== plan.preload.key) {
        load(vb, b, plan.preload);
      }
      return;
    }
    if (layers.current[b]?.key !== plan.show.key) load(vb, b, plan.show);
    if (vb.readyState < 2) return; // canplay calls tick again
    layers.current[b] = plan.show;
    vb.loop = plan.show.loop;
    if (Math.abs(vb.currentTime - plan.show.timeS) > 0.3) vb.currentTime = plan.show.timeS;
    vb.muted = muted;
    if (plan.playing) play(vb);
    else vb.pause();
    activeRef.current = b;
    setActive(b);
    va.muted = true;
    window.setTimeout(() => {
      if (activeRef.current === b) va.pause();
    }, 300);
  }, [load, play, sync]);

  useEffect(() => {
    tick();
  }, [timeline, items, playhead, clockOffsetMs, muted, tick]);

  useEffect(() => {
    const timer = window.setInterval(tick, TICK_MS);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(boundary.current);
    };
  }, [tick]);

  const fitClass = fit === "cover" ? "object-cover" : "object-contain";
  return (
    <div className={`relative overflow-hidden bg-black ${className}`}>
      {[refA, refB].map((ref, index) => (
        <video
          key={index}
          ref={ref}
          playsInline
          preload="auto"
          muted
          onCanPlay={tick}
          onLoadedData={tick}
          data-testid="BroadcastPlayer.layer"
          className={`absolute inset-0 h-full w-full ${fitClass} transition-opacity duration-200 ${
            showing && active === index ? "opacity-100" : "opacity-0"
          }`}
        />
      ))}
      {children}
    </div>
  );
}
