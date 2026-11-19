import { describe, expect, it, vi } from "vitest";
import { GLOBAL_CHAT_TOPIC, LiveKitChatSource, type ChatTextStreamRoom } from "./LiveKitChatSource";
import type { GlobalChatMessage } from "./globalChat";

interface FakeRoomState {
  handlers: Map<
    string,
    (reader: { readAll: () => Promise<string> }, info: { identity: string }) => Promise<void>
  >;
  unregistered: string[];
  sent: Array<{ text: string; options: { topic: string } }>;
}

function makeFakeRoom(): {
  room: ChatTextStreamRoom;
  state: FakeRoomState;
} {
  const state: FakeRoomState = {
    handlers: new Map(),
    unregistered: [],
    sent: [],
  };
  const room: ChatTextStreamRoom = {
    registerTextStreamHandler: ((
      topic: string,
      cb: (reader: { readAll: () => Promise<string> }, info: { identity: string }) => Promise<void>,
    ) => {
      state.handlers.set(topic, cb);
    }) as unknown as ChatTextStreamRoom["registerTextStreamHandler"],
    unregisterTextStreamHandler: ((topic: string) => {
      state.unregistered.push(topic);
      state.handlers.delete(topic);
    }) as ChatTextStreamRoom["unregisterTextStreamHandler"],
    localParticipant: {
      identity: "local",
      sendText: vi.fn(async (text: string, options: { topic: string }) => {
        state.sent.push({ text, options });
      }),
    },
  };
  return { room, state };
}

async function deliver(state: FakeRoomState, payload: string, identity = "remote") {
  const handler = state.handlers.get(GLOBAL_CHAT_TOPIC);
  if (!handler) throw new Error("No handler registered");
  await handler({ readAll: () => Promise.resolve(payload) }, { identity });
}

describe("LiveKitChatSource", () => {
  it("registers on the global_chat topic on attach", () => {
    const { room, state } = makeFakeRoom();
    const src = new LiveKitChatSource();
    src.attach(room);
    expect(state.handlers.has(GLOBAL_CHAT_TOPIC)).toBe(true);
  });

  it("emits a viewer message for a well-formed payload", async () => {
    const { room, state } = makeFakeRoom();
    const src = new LiveKitChatSource();
    const received: GlobalChatMessage[] = [];
    src.subscribe((m) => received.push(m));
    src.attach(room);
    await deliver(state, JSON.stringify({ handle: "ada", body: "hi" }));
    expect(received).toHaveLength(1);
    expect(received[0].authorKind).toBe("viewer");
    expect(received[0].authorHandle).toBe("ada");
    expect(received[0].text).toBe("hi");
  });

  it("ignores malformed JSON without throwing", async () => {
    const { room, state } = makeFakeRoom();
    const src = new LiveKitChatSource();
    const listener = vi.fn();
    src.subscribe(listener);
    src.attach(room);
    await deliver(state, "not json");
    expect(listener).not.toHaveBeenCalled();
  });

  it("ignores payloads with an empty body", async () => {
    const { room, state } = makeFakeRoom();
    const src = new LiveKitChatSource();
    const listener = vi.fn();
    src.subscribe(listener);
    src.attach(room);
    await deliver(state, JSON.stringify({ handle: "ada", body: "   " }));
    expect(listener).not.toHaveBeenCalled();
  });

  it("publishes via room.localParticipant.sendText on the chat topic", async () => {
    const { room, state } = makeFakeRoom();
    const src = new LiveKitChatSource();
    src.attach(room);
    await src.publish("ada", "broadcast me");
    expect(state.sent).toHaveLength(1);
    expect(state.sent[0].options.topic).toBe(GLOBAL_CHAT_TOPIC);
    const parsed = JSON.parse(state.sent[0].text);
    expect(parsed).toEqual({ handle: "ada", body: "broadcast me" });
  });

  it("publish is a no-op when not attached", async () => {
    const src = new LiveKitChatSource();
    await src.publish("ada", "hi"); // does not throw
  });

  it("detach unregisters the handler", () => {
    const { room, state } = makeFakeRoom();
    const src = new LiveKitChatSource();
    src.attach(room);
    src.detach();
    expect(state.unregistered).toEqual([GLOBAL_CHAT_TOPIC]);
  });
});
