/**
 * PullToRefresh — pointer-events-based pull-to-refresh wrapper (#420).
 *
 * Works with touch (iOS Safari) and pointer events (iPad trackpad).
 * Shows a circular progress indicator and cancels mid-pull if released early.
 * Honors `prefers-reduced-motion: reduce` by disabling the elastic overscroll.
 *
 * Props:
 *   - onRefresh: () => Promise<void> | void — called when pull threshold crossed.
 *   - children: ReactNode
 *   - threshold?: number — pixels to pull before triggering refresh (default 80)
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

interface PullToRefreshProps {
  children: ReactNode;
  onRefresh: () => void | Promise<void>;
  threshold?: number;
}

export function PullToRefresh({
  children,
  onRefresh,
  threshold = 80,
}: PullToRefreshProps) {
  const [pulling, setPulling] = useState(false);
  const [progress, setProgress] = useState(0);
  const startYRef = useRef<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const prefersReduced =
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      const el = containerRef.current;
      if (!el) return;
      // Only start pull when at top and pulling downward
      if (el.scrollTop > 2) return;
      startYRef.current = e.clientY;
      setPulling(true);
      setProgress(0);
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    },
    []
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (startYRef.current == null) return;
      const dy = e.clientY - startYRef.current;
      if (dy < 0) return; // ignore upward drags
      const damped = Math.min(dy * 0.5, threshold * 1.5);
      setProgress(Math.min(damped / threshold, 1));
    },
    [threshold]
  );

  const handlePointerUp = useCallback(async () => {
    if (startYRef.current == null) return;
    startYRef.current = null;
    if (progress >= 1) {
      setProgress(0);
      setPulling(false);
      await onRefresh();
    } else {
      // Cancelled mid-pull — spring back
      setProgress(0);
      setPulling(false);
    }
  }, [progress, onRefresh]);

  useEffect(() => {
    // Cancel if user scrolls away during pull
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      if (el.scrollTop > 2 && startYRef.current != null) {
        startYRef.current = null;
        setPulling(false);
        setProgress(0);
      }
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const offset = prefersReduced ? 0 : pulling ? Math.min(progress * threshold, threshold) : 0;

  return (
    <div
      ref={containerRef}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      style={{
        overflowY: "auto",
        overscrollBehaviorY: "contain",
        touchAction: "pan-y",
        position: "relative",
      }}
    >
      {/* Progress indicator */}
      {pulling && (
        <div
          aria-hidden={!pulling}
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: `${offset}px`,
            display: "flex",
            alignItems: "flex-end",
            justifyContent: "center",
            paddingBottom: "8px",
            pointerEvents: "none",
            overflow: "hidden",
            transition: prefersReduced ? "none" : "height 0.15s ease-out",
          }}
        >
          <div
            style={{
              width: "24px",
              height: "24px",
              borderRadius: "50%",
              border: "3px solid var(--border)",
              borderTopColor: progress >= 1 ? "var(--accent-green)" : "var(--accent-blue)",
              transform: `rotate(${progress * 360}deg)`,
              transition: prefersReduced ? "none" : "transform 0.1s linear",
            }}
          />
        </div>
      )}
      <div
        style={{
          transform: `translateY(${offset}px)`,
          transition: prefersReduced ? "none" : "transform 0.2s ease-out",
        }}
      >
        {children}
      </div>
    </div>
  );
}