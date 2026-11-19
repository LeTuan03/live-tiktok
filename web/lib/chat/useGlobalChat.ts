"use client";

// React glue for the global chat. The store lives across product
// changes inside a single mount — when a new pitch starts, the
// previous product's tail of comments is still on screen and gets
// pushed out by the new product's simulator over the next few
// seconds. That's the "single continuous stream" UX the consumer
// feed needs.
//
// The store is created INSIDE the effect (not via a lazy
// `useState(() => new …)`) so React Strict Mode's mount → cleanup →
// mount double-invoke in dev gets a *fresh* store on each cycle. The
// lazy-useState pattern would hand the same instance back on the
// second mount, but our cleanup disposes it on the first cycle, so
// the second mount inherits a dead store and every `addSource` no-ops.

import { useCallback, useEffect, useState } from "react";
import { GlobalChatStore } from "./GlobalChatStore";
import { SimulatedChatSource } from "./SimulatedChatSource";
import type { ChatMessageSource, GlobalChatMessage } from "./globalChat";

/**
 * Bridge surface this hook accepts. Matches the read-only
 * `ChatMessageSource` (subscribe / start / stop) plus an outbound
 * `publish(handle, body)` for the composer fan-out. Implemented by
 * `ChannelRoomCoordinator.getActiveChatBridge()` in production; tests
 * pass a hand-rolled stub.
 */
export interface GlobalChatBridge extends ChatMessageSource {
  publish(handle: string, body: string): Promise<void> | void;
}

export interface UseGlobalChatOptions {
  /** Product currently airing on the active channel. `null` pauses the
   *  simulator — the previous product's comments stay visible in the
   *  rolling buffer until a new product (or the user's composer) pushes
   *  them off. */
  productId: string | null;
  /** Optional override for the self handle (e.g. a provided display name when
   *  signed in). Defaults to "anon" in the store. */
  selfHandle?: string;
  /** Live cross-client transport. When provided, every user message
   *  fans out to other viewers on the same channel via LiveKit text
   *  streams, and inbound messages from other clients merge into the
   *  same rolling buffer the simulator feeds. Bridge identity is
   *  expected to be stable for the coordinator's lifetime — the store
   *  is recreated if it changes. */
  chatBridge?: GlobalChatBridge | null;
}

export interface UseGlobalChatReturn {
  messages: GlobalChatMessage[];
  /** Inject a user-typed message into the local stream and (when a
   *  bridge is wired) broadcast it to other clients. */
  send: (text: string) => void;
}

export function useGlobalChat(options: UseGlobalChatOptions): UseGlobalChatReturn {
  const { productId, selfHandle, chatBridge } = options;

  // The store is created in an effect (NOT via lazy useState) so each
  // Strict-Mode mount cycle in dev gets its own undisposed instance.
  // `null` on the very first render — every other effect gates on it.
  const [store, setStore] = useState<GlobalChatStore | null>(null);
  const [messages, setMessages] = useState<GlobalChatMessage[]>([]);

  // Create the store + wire the LiveKit bridge (if any) + start
  // mirroring its messages into React state. Recreates only when
  // `chatBridge` identity changes — in production the coordinator-
  // backed bridge is stable, so this fires once per mount.
  useEffect(() => {
    const created = new GlobalChatStore({
      sources: chatBridge ? [chatBridge as ChatMessageSource] : [],
      publishRemote: chatBridge ? (handle, body) => chatBridge.publish(handle, body) : undefined,
    });
    const unsub = created.subscribe(setMessages);
    setStore(created);
    return () => {
      unsub();
      created.dispose();
      setStore(null);
      setMessages([]);
    };
  }, [chatBridge]);

  // Apply selfHandle when it changes — does not touch the rolling
  // buffer (a sign-in / out shouldn't wipe what's on screen).
  useEffect(() => {
    if (!store || !selfHandle) return;
    store.setSelfHandle(selfHandle);
  }, [store, selfHandle]);

  // Hot-swap the per-product simulator on every productId change. The
  // store's buffer is *not* cleared — the previous product's tail of
  // comments persists until the new product's simulator (or the
  // composer / wire) pushes them out.
  useEffect(() => {
    if (!store || !productId) return;
    const simulator = new SimulatedChatSource({ productId });
    store.addSource(simulator);
    return () => {
      store.removeSource(simulator);
      simulator.dispose();
    };
  }, [productId, store]);

  const send = useCallback(
    (text: string) => {
      store?.sendLocal(text);
    },
    [store],
  );

  return { messages, send };
}
