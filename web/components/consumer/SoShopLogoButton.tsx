"use client";

// SoShop wordmark in the consumer top chrome doubles as the on-demand
// agent-session trigger. Tapping it kicks off the FastAPI proxy at
// `/api/agents/session`; while a session is active a Live · MM:SS chip
// floats next to the wordmark and ticks down once a second from the
// upstream `seconds_remaining` value (refetched every 5s while live).

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { useAgentSession } from "@/lib/agents/useAgentSession";
import { useToast } from "@/lib/ui/toast";

function formatRemaining(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function CountdownChip({ secondsRemaining }: { secondsRemaining: number }) {
  const [remaining, setRemaining] = useState(secondsRemaining);

  useEffect(() => {
    setRemaining(secondsRemaining);
  }, [secondsRemaining]);

  useEffect(() => {
    const id = setInterval(() => {
      setRemaining((r) => Math.max(0, r - 1));
    }, 1_000);
    return () => clearInterval(id);
  }, []);

  return (
    <span
      data-testid="SoShopLogoButton.chip"
      className="ml-2 inline-flex items-center rounded-full bg-emerald-500/90 px-2 py-0.5 text-[11px] font-semibold leading-none text-white shadow-[0_1px_2px_rgba(0,0,0,0.35)]"
    >
      Live · {formatRemaining(remaining)}
    </span>
  );
}

export function SoShopLogoButton() {
  const router = useRouter();
  const toast = useToast();
  const { status, isStarting, startError, start } = useAgentSession();

  // Surface the error toast once per failure transition. The hook
  // exposes the live error reference; comparing it against the
  // previously-surfaced one keeps re-renders from spamming the toast.
  const [lastSurfacedError, setLastSurfacedError] = useState<Error | null>(null);
  useEffect(() => {
    if (startError && startError !== lastSurfacedError) {
      toast.show({
        kind: "error",
        title: "Couldn't start the agents. Try again.",
      });
      setLastSurfacedError(startError);
    }
  }, [startError, lastSurfacedError, toast]);

  const handleClick = () => {
    start();
    router.push("/");
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={isStarting}
      aria-label="Start a SoShop agent session"
      className="-m-2 flex items-center p-2 disabled:opacity-70"
    >
      <span
        aria-hidden
        className="block text-[20px] font-extrabold leading-none tracking-tight text-white drop-shadow-[0_1px_2px_rgba(0,0,0,0.45)]"
      >
        SoShop
      </span>
      {status?.active && <CountdownChip secondsRemaining={status.secondsRemaining} />}
    </button>
  );
}
