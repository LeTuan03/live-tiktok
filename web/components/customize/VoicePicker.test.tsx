import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { CustomizeVoice } from "@/lib/api/customize";
import { listVoices } from "@/lib/api/customize";
import { VoicePicker } from "./VoicePicker";

vi.mock("@/lib/api/customize", () => ({
  listVoices: vi.fn(),
}));

const mockedListVoices = vi.mocked(listVoices);

function voice(overrides: Partial<CustomizeVoice> = {}): CustomizeVoice {
  return {
    voiceId: "v1",
    publicOwnerId: "owner-1",
    name: "Hale",
    previewUrl: null,
    category: "professional",
    gender: "male",
    age: "young",
    accent: "american",
    language: "en",
    useCase: "conversational",
    descriptive: "Smooth & persuasive",
    imageUrl: null,
    ...overrides,
  };
}

describe("VoicePicker", () => {
  it("renders the curated voices grouped by gender", async () => {
    mockedListVoices.mockResolvedValue([
      voice(),
      voice({ voiceId: "v2", name: "Aria", gender: "female" }),
    ]);
    render(<VoicePicker selectedVoiceId={null} onSelect={vi.fn()} />);

    expect(await screen.findByText("Hale")).toBeInTheDocument();
    expect(screen.getByText("Aria")).toBeInTheDocument();
    // Voices are split into labeled Male / Female sections.
    expect(screen.getByRole("heading", { name: "Male" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Female" })).toBeInTheDocument();
    // No params: the curated list takes only an AbortSignal.
    expect(mockedListVoices).toHaveBeenCalledWith(expect.any(AbortSignal));
  });

  it("calls onSelect with the voice id and owner when a card is clicked", async () => {
    mockedListVoices.mockResolvedValue([voice()]);
    const onSelect = vi.fn();
    render(<VoicePicker selectedVoiceId={null} onSelect={onSelect} />);

    const card = await screen.findByRole("radio");
    fireEvent.click(card);
    expect(onSelect).toHaveBeenCalledWith("v1", "owner-1");
  });
});
