// Browser-side helpers for the on-demand agent-session route. The route
// is a thin proxy in front of the FastAPI backend; this module is the
// only place where the snake_case wire shape leaks into the client.

export type AgentSessionStatus = {
  active: boolean;
  expiresAt: string | null;
  secondsRemaining: number;
  targetChannelCount: number;
};

type WireStatus = {
  active: boolean;
  expires_at: string | null;
  seconds_remaining: number;
  target_channel_count: number;
};

function adapt(wire: WireStatus): AgentSessionStatus {
  return {
    active: wire.active,
    expiresAt: wire.expires_at,
    secondsRemaining: wire.seconds_remaining,
    targetChannelCount: wire.target_channel_count,
  };
}

async function call(method: "GET" | "POST"): Promise<AgentSessionStatus> {
  const res = await fetch("/api/agents/session", { method });
  if (!res.ok) {
    throw new Error(`agent_session_${res.status}`);
  }
  const wire = (await res.json()) as WireStatus;
  return adapt(wire);
}

export function fetchAgentSessionStatus(): Promise<AgentSessionStatus> {
  return call("GET");
}

export function startAgentSession(): Promise<AgentSessionStatus> {
  return call("POST");
}
