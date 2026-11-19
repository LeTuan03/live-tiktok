// Local proxy for the agent-session endpoint exposed by the FastAPI
// backend. Swaps the request for the shared SITE_PASSWORD bearer token
// and pipes the upstream JSON response through unchanged.

export const runtime = "nodejs";

import { NextResponse } from "next/server";

function upstreamConfig():
  | { ok: true; baseUrl: string; password: string }
  | { ok: false; response: NextResponse } {
  const baseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  const password = process.env.SITE_PASSWORD;
  if (!baseUrl || !password) {
    return {
      ok: false,
      response: NextResponse.json({ error: "agent_proxy_unconfigured" }, { status: 503 }),
    };
  }
  return { ok: true, baseUrl: baseUrl.replace(/\/$/, ""), password };
}

async function proxy(method: "GET" | "POST"): Promise<NextResponse> {
  const cfg = upstreamConfig();
  if (!cfg.ok) return cfg.response;

  try {
    const upstream = await fetch(`${cfg.baseUrl}/agents/session`, {
      method,
      headers: { Authorization: `Bearer ${cfg.password}` },
      ...(method === "GET" ? { cache: "no-store" as const } : {}),
    });
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch {
    return NextResponse.json({ error: "upstream_unreachable" }, { status: 502 });
  }
}

export async function GET() {
  return proxy("GET");
}

export async function POST() {
  return proxy("POST");
}
