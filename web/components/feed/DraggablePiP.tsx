"use client";

// Picture-in-Picture wrapper that mirrors Apple's HIG for PiP windows on the
// consumer feed:
//
//   - Press-and-drag to reposition. On release, the PiP snaps to the nearest
//     corner of its parent container.
//   - Drag past an edge (more than half off-screen) to "stash" the PiP — it
//     slides off-screen leaving a thin handle visible at the edge.
//   - Press the handle and drag inward to restore the PiP. A pure tap on a
//     stashed PiP is ignored (a pure tap won't unstash; a drag is required).
//   - Tap (no movement past a small slop threshold) calls `onTap`. The feed
//     uses this to expand the product into the full-bleed slot.
//
// The component positions itself absolutely inside its nearest positioned
// ancestor and uses that ancestor's measured client size as the drag bounds.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

type StashEdge = "left" | "right";

interface Anchor {
  x: number;
  y: number;
  stashed: StashEdge | null;
}

interface DraggablePiPProps {
  width: number;
  height: number;
  /** Padding from the top of the parent. Keeps initial / snapped position
   *  clear of the top chrome (chips, mute button). */
  insetTop?: number;
  /** Padding from the bottom of the parent. Keeps it clear of the meta block. */
  insetBottom?: number;
  /** Padding from the left/right of the parent. */
  insetSide?: number;
  /** Visible width of the PiP when stashed off-screen. */
  handleWidth?: number;
  /** Which corner the PiP starts at. */
  initialCorner?: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  /** When true (default), releasing the PiP snaps it to the nearest corner of
   *  the parent and stashes it off-screen when dragged past an edge. When
   *  false, the PiP rests wherever the user drops it (clamped to the inset
   *  bounds), and edge-stash is disabled — desktop "drag anywhere" mode. */
  snapToCorners?: boolean;
  /** Fired on tap (pointer moved less than the slop threshold). */
  onTap?: () => void;
  /** Accessible name for the draggable surface. */
  ariaLabel?: string;
  /** Optional class merged onto the wrapper. */
  className?: string;
  children: ReactNode;
}

// Pointer travel below this is treated as a tap, above as a drag.
const DRAG_SLOP_PX = 6;

export function DraggablePiP({
  width,
  height,
  insetTop = 80,
  insetBottom = 160,
  insetSide = 16,
  handleWidth = 20,
  initialCorner = "top-right",
  snapToCorners = true,
  onTap,
  ariaLabel,
  className,
  children,
}: DraggablePiPProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [bounds, setBounds] = useState<{ w: number; h: number } | null>(null);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const [dragPos, setDragPos] = useState<{ x: number; y: number } | null>(null);
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    origX: number;
    origY: number;
    curX: number;
    curY: number;
    moved: boolean;
  } | null>(null);

  // Measure the offsetParent and follow resizes. PersonaCard's article is
  // `position: relative` with `h-[100dvh] w-full`, so the parent client
  // rect == the viewport while the card is active.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const parent = (el.offsetParent as HTMLElement | null) ?? el.parentElement;
    if (!parent) return;
    const measure = () => {
      setBounds({ w: parent.clientWidth, h: parent.clientHeight });
    };
    measure();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(parent);
    if (!ro) window.addEventListener("resize", measure);
    return () => {
      ro?.disconnect();
      if (!ro) window.removeEventListener("resize", measure);
    };
  }, []);

  // First placement once we have bounds.
  useEffect(() => {
    if (!bounds || anchor) return;
    const { w, h } = bounds;
    const x =
      initialCorner === "top-right" || initialCorner === "bottom-right"
        ? Math.max(insetSide, w - width - insetSide)
        : insetSide;
    const y =
      initialCorner === "top-left" || initialCorner === "top-right"
        ? insetTop
        : Math.max(insetTop, h - height - insetBottom);
    setAnchor({ x, y, stashed: null });
  }, [anchor, bounds, height, initialCorner, insetBottom, insetSide, insetTop, width]);

  // Re-clamp the anchor on viewport resize so it never ends up out of reach.
  useEffect(() => {
    if (!anchor || !bounds) return;
    const { w, h } = bounds;
    const clampedY = clamp(anchor.y, insetTop, Math.max(insetTop, h - height - insetBottom));
    let next: Anchor;
    if (anchor.stashed === "left") {
      next = { x: -(width - handleWidth), y: clampedY, stashed: "left" };
    } else if (anchor.stashed === "right") {
      next = { x: w - handleWidth, y: clampedY, stashed: "right" };
    } else {
      next = {
        x: clamp(anchor.x, insetSide, Math.max(insetSide, w - width - insetSide)),
        y: clampedY,
        stashed: null,
      };
    }
    if (next.x !== anchor.x || next.y !== anchor.y) setAnchor(next);
  }, [anchor, bounds, handleWidth, height, insetBottom, insetSide, insetTop, width]);

  // Compute the resting anchor for a freshly-released drag.
  const snap = useCallback(
    (rawX: number, rawY: number): Anchor => {
      if (!bounds) return { x: rawX, y: rawY, stashed: null };
      const { w, h } = bounds;
      // Free-form mode: clamp to the inset bounds and rest there. No corner
      // snap, no edge stash — desktop uses this so the user can park the
      // PiP anywhere it fits on the canvas.
      if (!snapToCorners) {
        return {
          x: clamp(rawX, insetSide, Math.max(insetSide, w - width - insetSide)),
          y: clamp(rawY, insetTop, Math.max(insetTop, h - height - insetBottom)),
          stashed: null,
        };
      }
      const cx = rawX + width / 2;
      // Stash when the center crosses an edge (more than half is off-screen).
      if (cx < 0) {
        return {
          x: -(width - handleWidth),
          y: clamp(rawY, insetTop, h - height - insetBottom),
          stashed: "left",
        };
      }
      if (cx > w) {
        return {
          x: w - handleWidth,
          y: clamp(rawY, insetTop, h - height - insetBottom),
          stashed: "right",
        };
      }
      const corners: Anchor[] = [
        { x: insetSide, y: insetTop, stashed: null },
        { x: w - width - insetSide, y: insetTop, stashed: null },
        { x: insetSide, y: h - height - insetBottom, stashed: null },
        { x: w - width - insetSide, y: h - height - insetBottom, stashed: null },
      ];
      let best = corners[0];
      let bestD = Infinity;
      for (const c of corners) {
        const d = (c.x - rawX) ** 2 + (c.y - rawY) ** 2;
        if (d < bestD) {
          bestD = d;
          best = c;
        }
      }
      return best;
    },
    [bounds, handleWidth, height, insetBottom, insetSide, insetTop, snapToCorners, width],
  );

  const onPointerDown = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      if (!anchor) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      drag.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startY: e.clientY,
        origX: anchor.x,
        origY: anchor.y,
        curX: anchor.x,
        curY: anchor.y,
        moved: false,
      };
      setDragPos({ x: anchor.x, y: anchor.y });
    },
    [anchor],
  );

  const onPointerMove = useCallback((e: PointerEvent<HTMLDivElement>) => {
    const s = drag.current;
    if (!s || s.pointerId !== e.pointerId) return;
    const dx = e.clientX - s.startX;
    const dy = e.clientY - s.startY;
    if (!s.moved && Math.hypot(dx, dy) > DRAG_SLOP_PX) s.moved = true;
    s.curX = s.origX + dx;
    s.curY = s.origY + dy;
    setDragPos({ x: s.curX, y: s.curY });
  }, []);

  const onPointerEnd = useCallback(
    (e: PointerEvent<HTMLDivElement>) => {
      const s = drag.current;
      if (!s || s.pointerId !== e.pointerId) return;
      const moved = s.moved;
      const finalX = s.curX;
      const finalY = s.curY;
      drag.current = null;
      setDragPos(null);
      if (!moved) {
        // Tap. Stashed PiPs ignore taps — Apple's pattern: drag to restore.
        if (anchor?.stashed) return;
        onTap?.();
        return;
      }
      setAnchor(snap(finalX, finalY));
    },
    [anchor, onTap, snap],
  );

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (anchor?.stashed) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onTap?.();
      }
    },
    [anchor, onTap],
  );

  const visible = anchor != null;
  const pos = dragPos ?? anchor ?? { x: 0, y: 0 };
  const dragging = dragPos != null;
  const stashedEdge = !dragging ? (anchor?.stashed ?? null) : null;

  return (
    <div
      ref={ref}
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      data-stashed={stashedEdge ?? undefined}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onKeyDown={onKeyDown}
      className={`absolute left-0 top-0 z-30 touch-none select-none ${className ?? ""}`}
      style={{
        width,
        height,
        transform: `translate3d(${pos.x}px, ${pos.y}px, 0)`,
        opacity: visible ? 1 : 0,
        transition: dragging ? "none" : "transform 220ms cubic-bezier(0.2, 0.8, 0.2, 1)",
        cursor: dragging ? "grabbing" : "grab",
        touchAction: "none",
        contain: "layout paint",
      }}
    >
      {children}
      {stashedEdge && (
        <div
          aria-hidden
          className={`pointer-events-none absolute top-1/2 flex h-12 w-1.5 -translate-y-1/2 items-center justify-center rounded-full bg-white/85 shadow ${
            stashedEdge === "left" ? "right-1" : "left-1"
          }`}
        />
      )}
    </div>
  );
}

function clamp(v: number, lo: number, hi: number): number {
  if (hi < lo) return lo;
  return Math.max(lo, Math.min(hi, v));
}
