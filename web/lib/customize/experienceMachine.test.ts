import { describe, expect, it } from "vitest";
import type { CustomHostSession } from "@/lib/api/customize";
import { type ExperienceState, INITIAL_EXPERIENCE, experienceReducer } from "./experienceMachine";

const SESSION: CustomHostSession = {
  roomName: "room-1",
  livekitUrl: "wss://livekit.example",
  token: "tok",
  personaId: "persona-1",
  sessionId: "session-1",
};

describe("experienceReducer", () => {
  it("starts in input mode with no error", () => {
    expect(INITIAL_EXPERIENCE).toEqual({ status: "input", error: null });
  });

  it("input --submit--> submitting", () => {
    expect(experienceReducer({ status: "input", error: null }, { type: "submit" })).toEqual({
      status: "submitting",
    });
  });

  it("submitting --submitSucceeded--> live, carrying the session", () => {
    expect(
      experienceReducer({ status: "submitting" }, { type: "submitSucceeded", session: SESSION }),
    ).toEqual({ status: "live", session: SESSION });
  });

  it("submitting --submitFailed--> input, carrying the error", () => {
    expect(
      experienceReducer({ status: "submitting" }, { type: "submitFailed", error: "boom" }),
    ).toEqual({ status: "input", error: "boom" });
  });

  it("live --edit--> input, clearing any prior error", () => {
    expect(experienceReducer({ status: "live", session: SESSION }, { type: "edit" })).toEqual({
      status: "input",
      error: null,
    });
  });

  it("ignores illegal transitions, leaving the state untouched", () => {
    const input: ExperienceState = { status: "input", error: null };
    // Can't reach live except from submitting.
    expect(experienceReducer(input, { type: "submitSucceeded", session: SESSION })).toBe(input);
    // Can't edit from input.
    expect(experienceReducer(input, { type: "edit" })).toBe(input);

    const submitting: ExperienceState = { status: "submitting" };
    // Can't submit twice.
    expect(experienceReducer(submitting, { type: "submit" })).toBe(submitting);
    // Can't bail to edit mid-submit.
    expect(experienceReducer(submitting, { type: "edit" })).toBe(submitting);

    const live: ExperienceState = { status: "live", session: SESSION };
    // A late success while already live is a no-op.
    expect(experienceReducer(live, { type: "submitSucceeded", session: SESSION })).toBe(live);
  });
});
