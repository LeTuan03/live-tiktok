// `@modal` parallel-slot fallback. Renders nothing when no
// intercepting route matches — keeps non-modal consumer
// routes (`/`, `/favorites`, cold-loaded `/persona`) free of the
// modal-cover overlay.
export default function ModalDefault() {
  return null;
}
