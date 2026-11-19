import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { CustomizeForm, type CustomizeFormValues } from "./CustomizeForm";

// The VoicePicker self-fetches the curated list (an array) on mount; stub the
// client so the form renders without hitting the network.
vi.mock("@/lib/api/customize", () => ({
  listVoices: vi.fn().mockResolvedValue([
    {
      voiceId: "v1",
      publicOwnerId: "owner-1",
      name: "Hale",
      previewUrl: null,
      category: null,
      gender: "male",
      age: null,
      accent: null,
      language: null,
      useCase: null,
      descriptive: "Smooth & persuasive",
      imageUrl: null,
    },
  ]),
  createCustomHost: vi.fn(),
}));

const inBandScript = Array.from({ length: 35 }, () => "word").join(" ");

function renderForm(overrides: {
  values?: Partial<CustomizeFormValues>;
  avatarPreviewUrl?: string | null;
  onSubmit?: () => void;
}) {
  const values: CustomizeFormValues = {
    voiceId: null,
    voicePublicOwnerId: null,
    script: "",
    ...overrides.values,
  };
  const onSubmit = overrides.onSubmit ?? vi.fn();
  render(
    <CustomizeForm
      values={values}
      avatarPreviewUrl={overrides.avatarPreviewUrl ?? null}
      avatarError={null}
      submitting={false}
      submitError={null}
      onChange={vi.fn()}
      onAvatarSelect={vi.fn()}
      onAvatarClear={vi.fn()}
      onSubmit={onSubmit}
    />,
  );
  return { onSubmit };
}

describe("CustomizeForm", () => {
  // The picker fetches on mount; let its resolved state land inside act().
  // The curated picker renders a "Male" section heading once loaded.
  const settlePicker = () => screen.findByRole("heading", { name: "Male" });

  it("disables 'Put on air' until avatar, voice and an in-band script are all set", async () => {
    renderForm({});
    await settlePicker();
    expect(screen.getByRole("button", { name: /put on air/i })).toBeDisabled();
  });

  it("shows the word counter for an in-band script", async () => {
    renderForm({ values: { script: inBandScript } });
    await settlePicker();
    expect(screen.getByText("35/45")).toBeInTheDocument();
  });

  it("counts an over-long script past the max", async () => {
    const tooLong = Array.from({ length: 60 }, () => "word").join(" ");
    renderForm({ values: { script: tooLong } });
    await settlePicker();
    expect(screen.getByText("60/45")).toBeInTheDocument();
  });

  it("enables submit and fires onSubmit once everything is valid", async () => {
    const onSubmit = vi.fn();
    renderForm({
      values: { voiceId: "v1", script: inBandScript },
      avatarPreviewUrl: "blob:preview",
      onSubmit,
    });
    await settlePicker();
    const button = screen.getByRole("button", { name: /put on air/i });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("orders the build sections Face, then Pitch, then Voice", async () => {
    renderForm({});
    await settlePicker();
    const face = screen.getByText("Face");
    const pitch = screen.getByText("Pitch");
    const voice = screen.getByText("Voice");
    // Each later label must follow the previous one in document order.
    expect(face.compareDocumentPosition(pitch) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(pitch.compareDocumentPosition(voice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("no longer renders the optional Name field", async () => {
    renderForm({});
    await settlePicker();
    expect(screen.queryByPlaceholderText("e.g. Mara")).toBeNull();
    expect(screen.queryByText("Name")).toBeNull();
  });
});
