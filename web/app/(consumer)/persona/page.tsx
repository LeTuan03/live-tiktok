"use client";

import { useSearchParams } from "next/navigation";
import { PersonaSurface } from "@/components/persona/PersonaSurface";

/**
 * AskAvatar surface: a 1-to-1 LiveKit room per viewer.
 *
 * Persona, script, and optional focused product are read from query
 * params so the route can serve as a deep-link target from product
 * pages. Defaults fall back to env-configured values (see
 * `NEXT_PUBLIC_PERSONA_DEFAULT_*`) for testing without a full feed
 * wiring.
 *
 * The backend is open access, so the surface renders for everyone.
 *
 * Idle warning (60s) and hard cap (10min) for D7/D20 are implemented
 * inside `PersonaSurface`; teardown propagates through `useRoomClient`
 * so a forced disconnect tears the room down cleanly.
 */
export default function PersonaPage() {
  const params = useSearchParams();
  const personaId =
    params.get("persona_id") ?? process.env.NEXT_PUBLIC_PERSONA_DEFAULT_ID ?? "default";
  const scriptId =
    params.get("script_id") ?? process.env.NEXT_PUBLIC_PERSONA_DEFAULT_SCRIPT_ID ?? "default";
  const productId = params.get("product_id");

  return <PersonaSurface personaId={personaId} scriptId={scriptId} productId={productId} />;
}
