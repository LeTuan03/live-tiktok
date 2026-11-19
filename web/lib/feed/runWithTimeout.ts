// Promise-wrapper that rejects with `TimeoutError` after `ms`. Used by
// the swap state machine to bound `coordinator.swap()` so a stuck
// LiveKit handshake surfaces as a retryable failure instead of hanging
// the UI.

export class TimeoutError extends Error {
  constructor() {
    super("timeout");
    this.name = "TimeoutError";
  }
}

export function runWithTimeout<T>(ms: number, op: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new TimeoutError());
    }, ms);
    op().then(
      (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}
