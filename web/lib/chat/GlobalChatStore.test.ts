import { describe, expect, it } from "vitest";
import { GlobalChatStore } from "./GlobalChatStore";
import type { ChatMessageSource, GlobalChatListener, GlobalChatMessage } from "./globalChat";

class StubSource implements ChatMessageSource {
  private listener: GlobalChatListener | null = null;
  startCount = 0;
  stopCount = 0;
  start() {
    this.startCount += 1;
  }
  stop() {
    this.stopCount += 1;
  }
  subscribe(listener: GlobalChatListener): () => void {
    this.listener = listener;
    return () => {
      this.listener = null;
    };
  }
  emit(msg: GlobalChatMessage): void {
    this.listener?.(msg);
  }
}

function makeMessage(id: string, text = id): GlobalChatMessage {
  return {
    id,
    authorKind: "viewer",
    authorHandle: "tester",
    text,
    createdAt: Date.now(),
  };
}

describe("GlobalChatStore", () => {
  it("starts the source on construction", () => {
    const source = new StubSource();
    new GlobalChatStore({ sources: [source] });
    expect(source.startCount).toBe(1);
  });

  it("trims the rolling buffer to bufferSize", () => {
    const source = new StubSource();
    const store = new GlobalChatStore({ sources: [source], bufferSize: 3 });
    for (let i = 0; i < 5; i++) source.emit(makeMessage(`m${i}`));
    expect(store.getMessages().map((m) => m.id)).toEqual(["m2", "m3", "m4"]);
  });

  it("notifies subscribers of the rolling array", () => {
    const source = new StubSource();
    const store = new GlobalChatStore({ sources: [source], bufferSize: 5 });
    const snapshots: number[] = [];
    store.subscribe((arr) => snapshots.push(arr.length));
    source.emit(makeMessage("a"));
    source.emit(makeMessage("b"));
    expect(snapshots).toEqual([0, 1, 2]);
  });

  it("sendLocal injects a self-tagged message", () => {
    const source = new StubSource();
    const store = new GlobalChatStore({ sources: [source], selfHandle: "me" });
    store.sendLocal("hello world");
    const last = store.getMessages().at(-1);
    expect(last?.authorKind).toBe("self");
    expect(last?.authorHandle).toBe("me");
    expect(last?.text).toBe("hello world");
  });

  it("sendLocal ignores empty/whitespace input", () => {
    const source = new StubSource();
    const store = new GlobalChatStore({ sources: [source] });
    store.sendLocal("   ");
    expect(store.getMessages()).toHaveLength(0);
  });

  it("dispose unsubscribes from the source and clears state", () => {
    const source = new StubSource();
    const store = new GlobalChatStore({ sources: [source] });
    source.emit(makeMessage("a"));
    store.dispose();
    source.emit(makeMessage("b"));
    expect(store.getMessages()).toEqual([]);
  });

  it("merges messages from multiple sources into one buffer", () => {
    const a = new StubSource();
    const b = new StubSource();
    const store = new GlobalChatStore({ sources: [a, b] });
    a.emit(makeMessage("a1"));
    b.emit(makeMessage("b1"));
    a.emit(makeMessage("a2"));
    expect(store.getMessages().map((m) => m.id)).toEqual(["a1", "b1", "a2"]);
  });

  it("hot-swapping a source preserves the existing rolling buffer", () => {
    const first = new StubSource();
    const second = new StubSource();
    const store = new GlobalChatStore({ sources: [first] });
    first.emit(makeMessage("a"));
    first.emit(makeMessage("b"));
    // Swap: detach `first`, attach `second`. The "a"/"b" messages must
    // stay in the buffer — that's the continuous-stream guarantee.
    store.removeSource(first);
    store.addSource(second);
    expect(store.getMessages().map((m) => m.id)).toEqual(["a", "b"]);
    // New messages from the new source land at the tail.
    second.emit(makeMessage("c"));
    expect(store.getMessages().map((m) => m.id)).toEqual(["a", "b", "c"]);
  });

  it("removeSource stops the source so pending emissions stop", () => {
    const source = new StubSource();
    const store = new GlobalChatStore({ sources: [source] });
    store.removeSource(source);
    expect(source.stopCount).toBe(1);
  });

  it("sendLocal also fans out to publishRemote with the self handle", () => {
    const source = new StubSource();
    const calls: Array<[string, string]> = [];
    const store = new GlobalChatStore({
      sources: [source],
      selfHandle: "ada",
      publishRemote: (handle, body) => {
        calls.push([handle, body]);
      },
    });
    store.sendLocal("hi room");
    expect(calls).toEqual([["ada", "hi room"]]);
  });
});
