// Finite-state machine for the /customize experience.
//
// The page is always in exactly one of two VISIBLE modes, "input" (the build
// form) or "live" (the streaming monitor), with a brief "submitting" transition
// while the host is dispatched. Modeling it as a small hand-rolled state machine
// (the State pattern) keeps the legal transitions in one place: the reducer is
// the single authority on what can follow what, so the UI can never land in an
// impossible mix like "submitting and live at once". A plain reducer is the
// right tool at this size; a library (XState) would be overkill for three states.
//
// Form values deliberately live OUTSIDE this machine (see useCustomHostForm), so
// they survive every transition: going back from "live" to "input" re-shows the
// untouched form. Framework-free on purpose, so it unit-tests without React.

import type { CustomHostSession } from "@/lib/api/customize";

export type ExperienceState =
  | { status: "input"; error: string | null }
  | { status: "submitting" }
  | { status: "live"; session: CustomHostSession };

export type ExperienceAction =
  | { type: "submit" } //                               input      -> submitting
  | { type: "submitSucceeded"; session: CustomHostSession } // submitting -> live
  | { type: "submitFailed"; error: string } //          submitting -> input(error)
  | { type: "edit" }; //                                live       -> input

export const INITIAL_EXPERIENCE: ExperienceState = { status: "input", error: null };

/**
 * Pure transition function. Each case is guarded by the state it may fire from;
 * an action that doesn't apply to the current state returns the state unchanged
 * (same reference), so illegal transitions are silently ignored rather than
 * corrupting the mode.
 */
export function experienceReducer(
  state: ExperienceState,
  action: ExperienceAction,
): ExperienceState {
  switch (action.type) {
    case "submit":
      return state.status === "input" ? { status: "submitting" } : state;
    case "submitSucceeded":
      return state.status === "submitting" ? { status: "live", session: action.session } : state;
    case "submitFailed":
      return state.status === "submitting" ? { status: "input", error: action.error } : state;
    case "edit":
      return state.status === "live" ? { status: "input", error: null } : state;
    default:
      return state;
  }
}
