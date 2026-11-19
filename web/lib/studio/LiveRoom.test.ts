import { describe, expect, it, vi } from "vitest";

// LiveKit is stubbed at the SDK boundary: no test opens a real room.
vi.mock("livekit-client", () => ({
  Room: class {},
  RoomEvent: {
    TrackSubscribed: "trackSubscribed",
    TrackUnsubscribed: "trackUnsubscribed",
    AudioPlaybackStatusChanged: "audioPlaybackChanged",
    Reconnecting: "reconnecting",
    Reconnected: "reconnected",
    Disconnected: "disconnected",
  },
  Track: { Kind: { Video: "video", Audio: "audio" } },
}));

import type { Room } from "livekit-client";
import {
  LIVE_CHAT_TOPIC,
  LIVE_STATE_TOPIC,
  LiveRoom,
  parseChatPayload,
  parseLiveStatePayload,
} from "./LiveRoom";

type Handler = (...args: unknown[]) => unknown;

function makeFakeRoom() {
  const events = new Map<string, Handler>();
  const streams = new Map<string, Handler>();
  const sendText = vi.fn().mockResolvedValue(undefined);
  const fake = {
    canPlaybackAudio: true,
    remoteParticipants: new Map(),
    localParticipant: { identity: "host-1", sendText },
    on: (event: string, handler: Handler) => {
      events.set(event, handler);
      return fake;
    },
    removeAllListeners: vi.fn(),
    registerTextStreamHandler: (topic: string, handler: Handler) => streams.set(topic, handler),
    unregisterTextStreamHandler: (topic: string) => streams.delete(topic),
    connect: vi.fn().mockResolvedValue(undefined),
    disconnect: vi.fn().mockResolvedValue(undefined),
    startAudio: vi.fn().mockResolvedValue(undefined),
  };
  const deliver = async (topic: string, text: string, identity = "viewer-9") => {
    await streams.get(topic)?.({ readAll: async () => text }, { identity });
  };
  return { fake, events, streams, sendText, deliver };
}

const connection = { livekitUrl: "ws://lk", token: "jwt", roomName: "live-s1" };

describe("payload parsers", () => {
  it("accepts {name, text} chat and rejects junk", () => {
    expect(parseChatPayload('{"name":"Lan","text":" hi "}')).toEqual({ name: "Lan", text: "hi" });
    expect(parseChatPayload('{"text":"hi"}')).toEqual({ name: "Khách", text: "hi" });
    expect(parseChatPayload('{"name":"Lan","text":"  "}')).toBeNull();
    expect(parseChatPayload("not json")).toBeNull();
    expect(parseChatPayload("[1]")).toBeNull();
  });

  it("stamps a pushed state that has no timestamp", () => {
    const now = () => new Date("2026-11-12T10:00:00Z");
    expect(parseLiveStatePayload('{"mode":"presenting","current_product_id":"p1"}', now)).toEqual({
      mode: "presenting",
      currentProductId: "p1",
      lastLine: null,
      updatedAt: "2026-11-12T10:00:00.000Z",
    });
    expect(
      parseLiveStatePayload('{"mode":"paused","updated_at":"2026-11-12T09:00:00Z"}', now)
        ?.updatedAt,
    ).toBe("2026-11-12T09:00:00Z");
    expect(parseLiveStatePayload('{"mode":"nope"}', now)).toBeNull();
    expect(parseLiveStatePayload("{", now)).toBeNull();
  });
});

describe("LiveRoom", () => {
  it("connects and reports the connected state", async () => {
    const { fake } = makeFakeRoom();
    const room = new LiveRoom(() => fake as unknown as Room);
    await room.connect(connection);
    expect(fake.connect).toHaveBeenCalledWith("ws://lk", "jwt");
    expect(room.getSnapshot().status).toBe("connected");
  });

  it("surfaces a failed connect with the error message", async () => {
    const { fake } = makeFakeRoom();
    fake.connect.mockRejectedValue(new Error("ice failed"));
    const room = new LiveRoom(() => fake as unknown as Room);
    await room.connect(connection);
    expect(room.getSnapshot()).toMatchObject({ status: "failed", error: "ice failed" });
  });

  it("collects chat from others and echoes own messages after the send resolves", async () => {
    const { fake, sendText, deliver } = makeFakeRoom();
    const room = new LiveRoom(() => fake as unknown as Room);
    await room.connect(connection);

    await deliver(LIVE_CHAT_TOPIC, '{"name":"Lan","text":"Còn hàng không shop?"}');
    await room.sendChat("Chủ phòng", " Còn nhé ");

    expect(sendText).toHaveBeenCalledWith('{"name":"Chủ phòng","text":"Còn nhé"}', {
      topic: LIVE_CHAT_TOPIC,
    });
    expect(room.getSnapshot().chat.map((m) => [m.name, m.text, m.mine])).toEqual([
      ["Lan", "Còn hàng không shop?", false],
      ["Chủ phòng", "Còn nhé", true],
    ]);
  });

  it("does not add the local echo when the send fails", async () => {
    const { fake, sendText } = makeFakeRoom();
    sendText.mockRejectedValue(new Error("offline"));
    const room = new LiveRoom(() => fake as unknown as Room);
    await room.connect(connection);
    await expect(room.sendChat("A", "hi")).rejects.toThrow("offline");
    expect(room.getSnapshot().chat).toEqual([]);
  });

  it("publishes pushed live state to subscribers", async () => {
    const { fake, deliver } = makeFakeRoom();
    const room = new LiveRoom(() => fake as unknown as Room);
    const seen: Array<string | undefined> = [];
    room.subscribe((snapshot) => seen.push(snapshot.liveState?.mode));
    await deliver(
      LIVE_STATE_TOPIC,
      '{"mode":"presenting","current_product_id":"p1","updated_at":"2026-11-12T10:00:00Z"}',
    );
    expect(room.getSnapshot().liveState).toMatchObject({
      mode: "presenting",
      currentProductId: "p1",
    });
    expect(seen).toEqual([undefined, "presenting"]);
  });

  it("tracks the model's video and audio and clears them on disconnect", async () => {
    const { fake, events } = makeFakeRoom();
    const room = new LiveRoom(() => fake as unknown as Room);
    const video = { kind: "video" };
    const audio = { kind: "audio" };
    events.get("trackSubscribed")?.(video);
    events.get("trackSubscribed")?.(audio);
    expect(room.getSnapshot()).toMatchObject({ videoTrack: video, audioTrack: audio });
    events.get("disconnected")?.();
    expect(room.getSnapshot()).toMatchObject({
      status: "disconnected",
      videoTrack: null,
      audioTrack: null,
    });
  });

  it("unregisters stream handlers and disconnects once", async () => {
    const { fake, streams } = makeFakeRoom();
    const room = new LiveRoom(() => fake as unknown as Room);
    await room.disconnect();
    await room.disconnect();
    expect(streams.size).toBe(0);
    expect(fake.disconnect).toHaveBeenCalledTimes(1);
  });
});
