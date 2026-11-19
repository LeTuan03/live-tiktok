"use client";

// Minimal in-house toast layer.
//
// Why not Sonner / react-hot-toast: the surface area we need is small
// (mutation feedback for favorites, sign-in friction, network retries),
// and an extra dependency on the public consumer bundle has a cost.
// Roughly 150 LOC keeps us in control of the visual language (Tailwind
// tokens) and avoids shipping another portal layer.
//
// Wire it up by rendering `<Toaster />` once at the root layout. Anywhere
// in the tree, call `useToast()` and invoke `.show(...)`. Toasts are
// keyed by an autoincrementing id; they auto-dismiss after `durationMs`
// (default 4s) and can be dismissed early by tap.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastKind = "info" | "success" | "error";

export interface ToastInput {
  kind?: ToastKind;
  /** Headline copy. Required. */
  title: string;
  /** Optional second-line context. */
  body?: string;
  /** Auto-dismiss timeout. Pass `0` to make the toast sticky. */
  durationMs?: number;
}

interface ToastRecord extends Required<Omit<ToastInput, "body">> {
  id: number;
  body?: string;
}

interface ToastContextValue {
  show: (input: ToastInput) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_DURATION_MS = 4_000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const nextIdRef = useRef(1);
  const timersRef = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const show = useCallback(
    (input: ToastInput): number => {
      const id = nextIdRef.current++;
      const record: ToastRecord = {
        id,
        kind: input.kind ?? "info",
        title: input.title,
        body: input.body,
        durationMs: input.durationMs ?? DEFAULT_DURATION_MS,
      };
      setToasts((prev) => [...prev, record]);
      if (record.durationMs > 0) {
        const timer = setTimeout(() => dismiss(id), record.durationMs);
        timersRef.current.set(id, timer);
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  const value = useMemo<ToastContextValue>(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast() called outside <ToastProvider>");
  }
  return ctx;
}

interface ToastViewportProps {
  toasts: ToastRecord[];
  onDismiss: (id: number) => void;
}

function ToastViewport({ toasts, onDismiss }: ToastViewportProps) {
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      className="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4"
    >
      {toasts.map((t) => (
        <ToastCard key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}

interface ToastCardProps {
  toast: ToastRecord;
  onDismiss: () => void;
}

function ToastCard({ toast, onDismiss }: ToastCardProps) {
  const kindClasses: Record<ToastKind, string> = {
    info: "bg-slate-900/90 text-slate-100 ring-slate-700",
    success: "bg-emerald-700/95 text-white ring-emerald-500",
    error: "bg-red-700/95 text-white ring-red-500",
  };
  return (
    <button
      type="button"
      onClick={onDismiss}
      data-testid={`Toast.${toast.kind}`}
      className={`pointer-events-auto w-full max-w-sm cursor-pointer rounded-xl px-4 py-3 text-left text-sm shadow-lg ring-1 backdrop-blur ${kindClasses[toast.kind]}`}
    >
      <div className="font-semibold">{toast.title}</div>
      {toast.body && <div className="mt-0.5 text-xs opacity-90">{toast.body}</div>}
    </button>
  );
}
