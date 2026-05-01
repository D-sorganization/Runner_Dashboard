import React, { useCallback, useRef, useState } from 'react';

interface PullToRefreshProps {
  onRefresh: () => Promise<void>;
  children: React.ReactNode;
  disabled?: boolean;
}

/**
 * Pointer-events-based pull-to-refresh component.
 * Works with touch, mouse, and trackpad (iPad).
 * Shows progress indicator, supports cancellation mid-pull,
 * and honors prefers-reduced-motion.
 */
export function PullToRefresh({ onRefresh, children, disabled }: PullToRefreshProps) {
  const [pulling, setPulling] = useState(false);
  const [progress, setProgress] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const startY = useRef<number | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const prefersReducedMotion =
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const threshold = 80; // px to trigger refresh
  const maxPull = 120; // max visual pull distance

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (disabled || refreshing) return;
      const el = containerRef.current;
      if (!el) return;
      // Only start if at top of scroll
      if (el.scrollTop > 0) return;
      startY.current = e.clientY;
      setPulling(true);
      setProgress(0);
      (e.target as HTMLElement).setPointerCapture(e.pointerId);
    },
    [disabled, refreshing]
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!pulling || startY.current === null) return;
      const dy = e.clientY - startY.current;
      if (dy < 0) return; // Ignore upward pulls
      const p = Math.min(dy / threshold, 1);
      setProgress(p);
      // Apply visual transform
      const el = containerRef.current;
      if (el && !prefersReducedMotion) {
        const translate = Math.min(dy * 0.5, maxPull);
        el.style.transform = `translateY(${translate}px)`;
      }
    },
    [pulling, prefersReducedMotion]
  );

  const handlePointerUp = useCallback(async () => {
    if (!pulling) return;
    setPulling(false);
    startY.current = null;

    // Reset visual transform
    const el = containerRef.current;
    if (el) {
      el.style.transform = '';
    }

    if (progress >= 1) {
      setRefreshing(true);
      try {
        await onRefresh();
      } finally {
        setRefreshing(false);
        setProgress(0);
      }
    } else {
      setProgress(0);
    }
  }, [pulling, progress, onRefresh]);

  return (
    <div
      ref={containerRef}
      className="pull-to-refresh"
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      style={{ touchAction: 'pan-y', position: 'relative' }}
    >
      {/* Progress indicator */}
      <div
        aria-hidden={!pulling && !refreshing}
        className="ptr-indicator"
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          height: '4px',
          background: 'var(--accent-green)',
          transform: `scaleX(${progress})`,
          transformOrigin: 'left',
          opacity: pulling || refreshing ? 1 : 0,
          transition: prefersReducedMotion ? 'none' : 'opacity 150ms',
        }}
      />
      {refreshing && (
        <div
          aria-live="polite"
          className="ptr-refreshing"
          style={{
            position: 'absolute',
            top: '8px',
            left: '50%',
            transform: 'translateX(-50%)',
            fontSize: '12px',
            color: 'var(--text-muted)',
          }}
        >
          Refreshing…
        </div>
      )}
      {children}
    </div>
  );
}