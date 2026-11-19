import { describe, expect, it, vi } from "vitest";
import {
  PitchEventSource,
  PRODUCT_PITCH_TOPIC,
  type PitchTextStreamRoom,
} from "@/lib/feed/PitchEventSource";

interface FakeRoomState {
  handlers: Map<
    string,
    (reader: { readAll: () => Promise<string> }, info: { identity: string }) => Promise<void>
  >;
  unregistered: string[];
}

function makeFakeRoom(): { room: PitchTextStreamRoom; state: FakeRoomState } {
  const state: FakeRoomState = {
    handlers: new Map(),
    unregistered: [],
  };
  const room: PitchTextStreamRoom = {
    registerTextStreamHandler: ((
      topic: string,
      cb: (reader: { readAll: () => Promise<string> }, info: { identity: string }) => Promise<void>,
    ) => {
      state.handlers.set(topic, cb);
    }) as PitchTextStreamRoom["registerTextStreamHandler"],
    unregisterTextStreamHandler: ((topic: string) => {
      state.unregistered.push(topic);
      state.handlers.delete(topic);
    }) as PitchTextStreamRoom["unregisterTextStreamHandler"],
  };
  return { room, state };
}

async function deliver(state: FakeRoomState, payload: string): Promise<void> {
  const handler = state.handlers.get(PRODUCT_PITCH_TOPIC);
  if (!handler) throw new Error("No handler registered");
  await handler({ readAll: () => Promise.resolve(payload) }, { identity: "agent" });
}

describe("PitchEventSource", () => {
  it("registers on the canonical product_pitch topic on attach", () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    src.attach(room);
    expect(state.handlers.has(PRODUCT_PITCH_TOPIC)).toBe(true);
  });

  it("emits a show event for a well-formed show payload", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const events: unknown[] = [];
    src.subscribe((e) => events.push(e));
    src.attach(room);
    await deliver(
      state,
      JSON.stringify({
        type: "show",
        product_id: "abc",
        popup: { title: "Earbuds", image_url: "https://example/x.jpg" },
      }),
    );
    expect(events).toEqual([
      {
        kind: "show",
        productId: "abc",
        popup: { title: "Earbuds", image_url: "https://example/x.jpg" },
      },
    ]);
  });

  it("emits a dismiss event for a well-formed dismiss payload", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const events: unknown[] = [];
    src.subscribe((e) => events.push(e));
    src.attach(room);
    await deliver(state, JSON.stringify({ type: "dismiss", product_id: "abc" }));
    expect(events).toEqual([{ kind: "dismiss", productId: "abc" }]);
  });

  it("ignores malformed JSON without throwing", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const listener = vi.fn();
    src.subscribe(listener);
    src.attach(room);
    await deliver(state, "this is not json");
    expect(listener).not.toHaveBeenCalled();
  });

  it("ignores payloads with unknown type", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const listener = vi.fn();
    src.subscribe(listener);
    src.attach(room);
    await deliver(state, JSON.stringify({ type: "purr", product_id: "abc" }));
    expect(listener).not.toHaveBeenCalled();
  });

  it("ignores payloads missing product_id", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const listener = vi.fn();
    src.subscribe(listener);
    src.attach(room);
    await deliver(state, JSON.stringify({ type: "show", popup: {} }));
    expect(listener).not.toHaveBeenCalled();
  });

  it("detach unregisters the handler", () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    src.attach(room);
    src.detach();
    expect(state.unregistered).toEqual([PRODUCT_PITCH_TOPIC]);
  });

  it("subscribe returns an unsubscribe function", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const listener = vi.fn();
    const unsub = src.subscribe(listener);
    src.attach(room);
    unsub();
    await deliver(state, JSON.stringify({ type: "dismiss", product_id: "x" }));
    expect(listener).not.toHaveBeenCalled();
  });

  it("emitForTest delivers to all subscribers without a Room", () => {
    const src = new PitchEventSource();
    const a = vi.fn();
    const b = vi.fn();
    src.subscribe(a);
    src.subscribe(b);
    src.emitForTest({ kind: "dismiss", productId: "abc" });
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("dispose unregisters the handler and drops listeners", async () => {
    const { room, state } = makeFakeRoom();
    const src = new PitchEventSource();
    const listener = vi.fn();
    src.subscribe(listener);
    src.attach(room);
    src.dispose();
    expect(state.unregistered).toEqual([PRODUCT_PITCH_TOPIC]);
    src.emitForTest({ kind: "dismiss", productId: "x" });
    expect(listener).not.toHaveBeenCalled();
  });
});
