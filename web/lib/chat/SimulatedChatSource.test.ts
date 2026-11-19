import { describe, expect, it } from "vitest";
import { SimulatedChatSource, type Scheduler, type ScheduledHandle } from "./SimulatedChatSource";
import type { GlobalChatMessage } from "./globalChat";

interface FakeScheduledTask {
  ms: number;
  handler: () => void;
  cancelled: boolean;
}

class FakeScheduler implements Scheduler {
  readonly tasks: FakeScheduledTask[] = [];
  private clock = 0;
  setTimeout(handler: () => void, ms: number): ScheduledHandle {
    const task = { ms: this.clock + ms, handler, cancelled: false };
    this.tasks.push(task);
    return task;
  }
  clearTimeout(handle: ScheduledHandle): void {
    (handle as FakeScheduledTask).cancelled = true;
  }
  now(): number {
    return this.clock;
  }
  advance(toMs: number): void {
    this.clock = toMs;
    for (const task of this.tasks) {
      if (task.cancelled) continue;
      if (task.ms <= toMs) {
        task.cancelled = true;
        task.handler();
      }
    }
  }
}

describe("SimulatedChatSource", () => {
  it("emits messages at their scheduled offsets", () => {
    const scheduler = new FakeScheduler();
    const source = new SimulatedChatSource({
      productId: "p",
      scheduler,
      script: [
        { atMs: 100, authorHandle: "alice", text: "first" },
        { atMs: 250, authorHandle: "bob", text: "second" },
      ],
    });

    const received: GlobalChatMessage[] = [];
    source.subscribe((m) => received.push(m));
    source.start();

    scheduler.advance(150);
    expect(received.map((m) => m.text)).toEqual(["first"]);

    scheduler.advance(300);
    expect(received.map((m) => m.text)).toEqual(["first", "second"]);
  });

  it("stop() cancels pending emissions", () => {
    const scheduler = new FakeScheduler();
    const source = new SimulatedChatSource({
      productId: "p",
      scheduler,
      script: [
        { atMs: 100, authorHandle: "alice", text: "first" },
        { atMs: 500, authorHandle: "bob", text: "second" },
      ],
    });

    const received: GlobalChatMessage[] = [];
    source.subscribe((m) => received.push(m));
    source.start();

    scheduler.advance(150);
    source.stop();
    scheduler.advance(600);

    expect(received.map((m) => m.text)).toEqual(["first"]);
  });

  it("subscribe is observable across multiple listeners", () => {
    const scheduler = new FakeScheduler();
    const source = new SimulatedChatSource({
      productId: "p",
      scheduler,
      script: [{ atMs: 50, authorHandle: "x", text: "hi" }],
    });

    const a: string[] = [];
    const b: string[] = [];
    source.subscribe((m) => a.push(m.text));
    source.subscribe((m) => b.push(m.text));
    source.start();
    scheduler.advance(100);

    expect(a).toEqual(["hi"]);
    expect(b).toEqual(["hi"]);
  });
});
