"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { GatedAction } from "@/components/auth/GatedAction";
import { useRoomClient } from "@/components/lib/livekit";
import {
  type CreatePersonaSessionRequest,
  PersonaApiError,
  type PersonaSessionCreated,
  createPersonaSession,
} from "@/lib/api/persona";
import { AvatarStage } from "./AvatarStage";
import { MicPermissionGate } from "./MicPermissionGate";
import { PersonaProductPanel } from "./PersonaProductPanel";

interface PersonaSurfaceProps {
  /** Persona to dispatch into the new room. */
  personaId: string;
  /** Persona script for the dispatch metadata. */
  scriptId: string;
  /** Optional product the avatar should focus on. */
  productId?: string | null;
}

type ProvisionState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; session: PersonaSessionCreated }
  | { kind: "error"; message: string }
  | { kind: "non_avatar"; session: PersonaSessionCreated };

/**
 * Idle warning threshold: show a "still there?" prompt after 60s of no
 * user mic activity.
 */
const IDLE_WARN_MS = 60_000;

/**
 * Hard cap (D7 / D20): force-disconnect after 10 minutes regardless of
 * activity. Bounds the per-session cost so a runaway tab can't burn
 * avatar minutes indefinitely. The server-side budget cap (`non_avatar`
 * downgrade) covers the daily aggregate; this timer covers a single
 * session and is the client-visible counterpart.
 */
const HARD_CAP_MS = 10 * 60_000;

/**
 * Top-level AskAvatar surface. Provisions a 1-to-1 LiveKit session via
 * `/feed/session`, connects the shared `RoomClient` to the returned
 * room, and arranges the layout — full-bleed avatar on mobile, side-
 * by-side product panel + corner avatar on desktop. The room is keyed
 * by user (server-supplied `room_name`) so each viewer gets its own.
 */
export function PersonaSurface({ personaId, scriptId, productId }: PersonaSurfaceProps) {
  const { client, snapshot } = useRoomClient({ autoPublishMic: false });
  const [provision, setProvision] = useState<ProvisionState>({ kind: "idle" });
  const [hasStarted, setHasStarted] = useState(false);
  const [idleWarning, setIdleWarning] = useState(false);
  const [hardCapped, setHardCapped] = useState(false);
  // Tracks the last user-driven activity (mic toggle / session start)
  // so the idle timer can reset cleanly. A mounted-tab user counts as
  // active until they actively go silent. `lastActivityRef` is bumped on
  // each mic toggle or session start.
  const lastActivityRef = useRef<number>(Date.now());

  useEffect(() => {
    // Wait for the user to tap the Start CTA before provisioning, so a
    // session is only minted on an explicit tap.
    if (!hasStarted) return;

    let cancelled = false;
    (async () => {
      setProvision({ kind: "loading" });
      try {
        const body: CreatePersonaSessionRequest = {
          first_persona: {
            persona_id: personaId,
            script_id: scriptId,
            product_id: productId ?? null,
          },
        };
        // Backend is open access, so the session POST carries no token.
        const session = await createPersonaSession("", body);
        if (cancelled) return;
        if (!session.livekit_url || !session.livekit_token) {
          // Cost cap hit — server downgraded to non-avatar mode.
          setProvision({ kind: "non_avatar", session });
          return;
        }
        setProvision({ kind: "ready", session });
        await client.connect(session.livekit_url, session.livekit_token);
      } catch (err) {
        if (cancelled) return;
        const message =
          err instanceof PersonaApiError
            ? `${err.detail} (HTTP ${err.status})`
            : err instanceof Error
              ? err.message
              : String(err);
        setProvision({ kind: "error", message });
      }
    })();

    return () => {
      cancelled = true;
    };
    // `client` is stable (useMemo'd in `useRoomClient`), but listing it
    // makes the lifecycle explicit — every change to persona/script/
    // product re-provisions a fresh session.
  }, [hasStarted, personaId, scriptId, productId, client]);

  // The room is also disconnected on unmount by `useRoomClient`. We
  // additionally hook `pagehide` so iOS Safari, which doesn't always
  // fire React unmount on a hard navigation, still tears the room down.
  useEffect(() => {
    function handlePageHide() {
      void client.disconnect();
    }
    window.addEventListener("pagehide", handlePageHide);
    return () => window.removeEventListener("pagehide", handlePageHide);
  }, [client]);

  // Idle warning + hard cap (D7 / D20). Only armed once provisioning
  // has resolved successfully — there's no point counting idle time
  // against a session that isn't live yet. Both timers are torn down
  // when the dependency set changes (re-provision, unmount) so a stale
  // timer can't fire on a fresh session.
  useEffect(() => {
    if (provision.kind !== "ready") return;
    setHardCapped(false);
    setIdleWarning(false);
    lastActivityRef.current = Date.now();

    const idleTimer = window.setInterval(() => {
      // Mic published == user is actively engaging; suppress the
      // warning and keep the activity stamp fresh so a long-running
      // conversation doesn't trip the prompt mid-flow.
      if (snapshot.micPublished) {
        lastActivityRef.current = Date.now();
        setIdleWarning(false);
        return;
      }
      const elapsed = Date.now() - lastActivityRef.current;
      setIdleWarning(elapsed >= IDLE_WARN_MS);
    }, 1_000);

    const hardCap = window.setTimeout(() => {
      setHardCapped(true);
      void client.disconnect();
    }, HARD_CAP_MS);

    return () => {
      window.clearInterval(idleTimer);
      window.clearTimeout(hardCap);
    };
  }, [provision.kind, snapshot.micPublished, client]);

  const startMic = useCallback(() => {
    lastActivityRef.current = Date.now();
    setIdleWarning(false);
    return client.publishMicrophone();
  }, [client]);
  const stopMic = useCallback(() => client.unpublishMicrophone(), [client]);

  const placeholderLabel = (() => {
    if (hardCapped) {
      return "Session ended after 10 minutes. Refresh to start a new one.";
    }
    switch (provision.kind) {
      case "idle":
      case "loading":
        return "Connecting to your avatar…";
      case "non_avatar":
        return "Daily avatar minutes used. Come back tomorrow.";
      case "error":
        return provision.message;
      case "ready":
        switch (snapshot.state.kind) {
          case "connecting":
            return "Connecting to your avatar…";
          case "reconnecting":
            return "Reconnecting…";
          case "failed":
            return snapshot.state.error;
          case "disconnected":
            return "Disconnected.";
          default:
            return "Waiting for the avatar to start streaming…";
        }
    }
  })();

  const isErrored =
    provision.kind === "error" ||
    provision.kind === "non_avatar" ||
    snapshot.state.kind === "failed" ||
    hardCapped;

  // Permanent AI-disclosure chip. Rendered into both the mobile and
  // desktop layouts below so a viewer can never have it scrolled
  // off-screen.
  const disclosure = (
    <span
      role="note"
      aria-label="You are talking to an AI avatar"
      title="The avatar in this conversation is generated by AI."
      data-testid="ai-disclosure"
      className="pointer-events-none inline-flex items-center gap-1 rounded-full bg-black/60 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-white shadow ring-1 ring-white/15 backdrop-blur"
    >
      <span aria-hidden>●</span>
      AI avatar
    </span>
  );

  // Pre-start gate: tapping Start flips `hasStarted`, and that single
  // state change is what kicks the provisioning effect above.
  if (!hasStarted) {
    return (
      <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950 px-6 text-center text-slate-100">
        <div className="flex max-w-sm flex-col items-center gap-4">
          <div className="absolute right-3 top-3">{disclosure}</div>
          <h1 className="text-2xl font-semibold">Talk to the AI avatar</h1>
          <p className="text-sm text-slate-300">
            A free 1-to-1 conversation with the AI shopping presenter. Tap below to start a session.
          </p>
          <GatedAction onAction={() => setHasStarted(true)}>
            <button
              type="button"
              data-testid="askavatar-start"
              className="rounded-full bg-brand-accent px-5 py-2 text-sm font-semibold text-white"
            >
              Start AskAvatar
            </button>
          </GatedAction>
          {productId && (
            <p className="text-xs text-slate-400">
              About product&nbsp;
              <code className="rounded bg-black/40 px-1 py-0.5 font-mono">{productId}</code>
            </p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-40 bg-slate-950 text-slate-100">
      {/* Mobile: full-bleed avatar, mic button floating at the bottom. */}
      <div className="md:hidden">
        <div className="relative h-full w-full">
          <AvatarStage
            videoTrack={snapshot.remoteVideoTrack}
            audioTrack={snapshot.remoteAudioTrack}
            placeholderLabel={placeholderLabel}
          />
          <div className="pointer-events-none absolute right-3 top-3 z-30">{disclosure}</div>
          {idleWarning && !hardCapped && (
            <div
              role="status"
              aria-live="polite"
              data-testid="idle-warning"
              className="pointer-events-none absolute left-1/2 top-16 z-30 -translate-x-1/2 rounded-full bg-amber-500/95 px-3 py-1 text-xs font-medium text-amber-950 shadow"
            >
              Still there? Tap the mic to keep going.
            </div>
          )}
          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 bg-gradient-to-t from-black/70 to-transparent px-4 pb-8 pt-16">
            <div className="pointer-events-auto">
              <MicPermissionGate
                micPublished={snapshot.micPublished}
                onRequestMic={startMic}
                onStopMic={stopMic}
                disabled={isErrored || provision.kind !== "ready"}
              />
            </div>
            {productId && (
              <p className="pointer-events-none text-xs text-slate-300">
                About product&nbsp;
                <code className="rounded bg-black/40 px-1 py-0.5 font-mono">{productId}</code>
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Desktop: split layout — product panel left, avatar corner right. */}
      <div className="hidden md:grid md:h-full md:grid-cols-[1fr_24rem] md:bg-slate-50 md:text-slate-900">
        <div className="overflow-hidden bg-white">
          <PersonaProductPanel productId={productId ?? null} personaId={personaId} />
        </div>
        <aside className="relative flex flex-col gap-4 border-l border-slate-200 bg-slate-950 p-4">
          <div className="absolute right-3 top-3 z-30">{disclosure}</div>
          <AvatarStage
            videoTrack={snapshot.remoteVideoTrack}
            audioTrack={snapshot.remoteAudioTrack}
            corner
            placeholderLabel={placeholderLabel}
          />
          <MicPermissionGate
            micPublished={snapshot.micPublished}
            onRequestMic={startMic}
            onStopMic={stopMic}
            disabled={isErrored || provision.kind !== "ready"}
          />
          {idleWarning && !hardCapped && (
            <p
              role="status"
              aria-live="polite"
              data-testid="idle-warning"
              className="text-xs text-amber-300"
            >
              Still there? Tap the mic to keep going.
            </p>
          )}
          {hardCapped && (
            <p role="status" className="text-xs text-rose-300">
              Session ended after 10 minutes. Refresh to start a new one.
            </p>
          )}
          <ConnectionLine provisionKind={provision.kind} roomKind={snapshot.state.kind} />
        </aside>
      </div>
    </div>
  );
}

function ConnectionLine({
  provisionKind,
  roomKind,
}: {
  provisionKind: ProvisionState["kind"];
  roomKind: ReturnType<typeof useRoomClient>["snapshot"]["state"]["kind"];
}) {
  let label: string;
  if (provisionKind === "error") label = "Could not start session";
  else if (provisionKind === "non_avatar") label = "Daily avatar minutes reached";
  else if (provisionKind === "loading") label = "Provisioning session…";
  else if (roomKind === "connected") label = "Connected to avatar room";
  else if (roomKind === "reconnecting") label = "Reconnecting…";
  else if (roomKind === "failed") label = "Connection failed";
  else label = "Connecting…";
  return <p className="text-xs text-slate-300">{label}</p>;
}
