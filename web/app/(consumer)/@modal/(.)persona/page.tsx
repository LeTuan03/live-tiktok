"use client";

import { useSearchParams } from "next/navigation";
import { PersonaSurface } from "@/components/persona/PersonaSurface";
import { ModalCover } from "@/components/consumer/ModalCover";

// Intercepting variant of `/persona`: a modal-cover presentation.
// Tapping Ask-Avatar from
// a Feed card opens the surface as an overlay; cold-loading the same
// URL still works as a full-page deep link. The backend is open access,
// so the surface renders for everyone.

export default function InterceptedPersonaModal() {
  const params = useSearchParams();
  const personaId =
    params.get("persona_id") ?? process.env.NEXT_PUBLIC_PERSONA_DEFAULT_ID ?? "default";
  const scriptId =
    params.get("script_id") ?? process.env.NEXT_PUBLIC_PERSONA_DEFAULT_SCRIPT_ID ?? "default";
  const productId = params.get("product_id");

  return (
    <ModalCover closeLabel="Close AskAvatar">
      <PersonaSurface personaId={personaId} scriptId={scriptId} productId={productId} />
    </ModalCover>
  );
}
