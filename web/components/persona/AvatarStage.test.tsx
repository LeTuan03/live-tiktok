import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { RemoteAudioTrack, RemoteVideoTrack } from "livekit-client";
import { AvatarStage } from "./AvatarStage";

function makeVideoTrack(): RemoteVideoTrack & {
  attach: ReturnType<typeof vi.fn>;
  detach: ReturnType<typeof vi.fn>;
} {
  const element = document.createElement("video");
  return {
    attach: vi.fn(() => element),
    detach: vi.fn(),
  } as unknown as RemoteVideoTrack & {
    attach: ReturnType<typeof vi.fn>;
    detach: ReturnType<typeof vi.fn>;
  };
}

function makeAudioTrack(): RemoteAudioTrack & {
  attach: ReturnType<typeof vi.fn>;
  detach: ReturnType<typeof vi.fn>;
} {
  const element = document.createElement("audio");
  return {
    attach: vi.fn(() => element),
    detach: vi.fn(),
  } as unknown as RemoteAudioTrack & {
    attach: ReturnType<typeof vi.fn>;
    detach: ReturnType<typeof vi.fn>;
  };
}

describe("AvatarStage", () => {
  it("renders the placeholder label when there is no video track", () => {
    render(
      <AvatarStage
        videoTrack={null}
        audioTrack={null}
        placeholderLabel="Connecting to your avatar…"
      />,
    );
    expect(screen.getByText(/connecting to your avatar/i)).toBeInTheDocument();
    // The "AI avatar" status pill is always present.
    expect(screen.getByText(/ai avatar/i)).toBeInTheDocument();
  });

  it("attaches the video track and hides the placeholder once a track exists", () => {
    const video = makeVideoTrack();
    render(<AvatarStage videoTrack={video} audioTrack={null} placeholderLabel="Connecting…" />);
    expect(video.attach).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Connecting…")).toBeNull();
  });

  it("attaches the audio track when supplied", () => {
    const audio = makeAudioTrack();
    render(<AvatarStage videoTrack={null} audioTrack={audio} placeholderLabel="Connecting…" />);
    expect(audio.attach).toHaveBeenCalledTimes(1);
  });

  it("detaches both tracks on unmount", () => {
    const video = makeVideoTrack();
    const audio = makeAudioTrack();
    const { unmount } = render(
      <AvatarStage videoTrack={video} audioTrack={audio} placeholderLabel="Connecting…" />,
    );
    unmount();
    expect(video.detach).toHaveBeenCalled();
    expect(audio.detach).toHaveBeenCalled();
  });

  it("applies the corner-panel layout classes when corner=true", () => {
    const { container } = render(
      <AvatarStage videoTrack={null} audioTrack={null} placeholderLabel="Connecting…" corner />,
    );
    expect(container.firstChild).toHaveClass("aspect-[3/4]");
  });
});
