// State machine for a single persona swap. The case names and transitions
// are kept stable so dashboards and traces aggregate consistently.
//
//   idle → preWarming → swapping → idle
//          (or skip preWarming in single-room mode)
//
// Any non-`idle` state means the renderer is mid-flight and the shimmer
// should mask the LiveKit warmup until the new agent's track binds.

export type SwapState =
  | { kind: "idle" }
  | { kind: "preWarming"; personaId: string }
  | { kind: "swapping"; personaId: string }
  | { kind: "failed"; message: string };

export const SwapState = {
  idle: (): SwapState => ({ kind: "idle" }),
  preWarming: (personaId: string): SwapState => ({ kind: "preWarming", personaId }),
  swapping: (personaId: string): SwapState => ({ kind: "swapping", personaId }),
  failed: (message: string): SwapState => ({ kind: "failed", message }),
} as const;

export function isInFlight(state: SwapState): boolean {
  return state.kind === "preWarming" || state.kind === "swapping";
}
