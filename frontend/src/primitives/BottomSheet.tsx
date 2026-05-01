/**
 * BottomSheet primitive — mobile-native action sheet pattern (issue #196).
 *
 * Features:
 * - Slides up from the bottom of the screen
 * - Backdrop click closes the sheet
 * - Escape key closes the sheet
 * - role="dialog", aria-modal="true", focus trap (Tab / Shift+Tab)
 * - Focus moves to first interactive element on open; returns to trigger on close
 * - Reduced-motion: slide animation disabled when prefers-reduced-motion is set
 */
import React, { useCallback, useEffect, useId, useRef } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function getFocusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export interface BottomSheetProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
}

export function BottomSheet({
  isOpen,
  onClose,
  title,
  children,
}: BottomSheetProps): React.ReactElement | null {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<Element | null>(null);
  const reduced = prefersReducedMotion();

  // Remember the element that had focus before the sheet opened.
  useEffect(() => {
    if (isOpen) {
      triggerRef.current = document.activeElement;
    }
  }, [isOpen]);

  // Focus first interactive element when sheet opens.
  useEffect(() => {
    if (!isOpen) return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = getFocusable(panel);
    if (focusable.length > 0) {
      focusable[0].focus();
    } else {
      panel.focus();
    }
  }, [isOpen]);

  // Restore focus to trigger when sheet closes.
  useEffect(() => {
    if (!isOpen && triggerRef.current instanceof HTMLElement) {
      triggerRef.current.focus();
      triggerRef.current = null;
    }
  }, [isOpen]);

  // Escape key closes the sheet.
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", handler, true);
    return () => document.removeEventListener("keydown", handler, true);
  }, [isOpen, onClose]);

  // Focus trap: Tab / Shift+Tab cycle within the panel.
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = getFocusable(panel);
      if (focusable.length === 0) {
        e.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    },
    [],
  );

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (e.target === e.currentTarget) {
        onClose();
      }
    },
    [onClose],
  );

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        aria-hidden="true"
        className="bottom-sheet-backdrop"
        onClick={handleBackdropClick}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15,17,23,0.6)",
          zIndex: 9998,
          animation: reduced ? undefined : "bottomSheetOverlayIn 200ms ease",
        }}
      />
      {/* Panel */}
      <div
        ref={panelRef}
        aria-labelledby={title ? titleId : undefined}
        aria-modal="true"
        className="bottom-sheet"
        onKeyDown={handleKeyDown}
        role="dialog"
        tabIndex={-1}
        style={{
          position: "fixed",
          left: 0,
          right: 0,
          bottom: 0,
          zIndex: 9999,
          background: "var(--bg-secondary)",
          borderTop: "1px solid var(--border)",
          borderRadius: "16px 16px 0 0",
          boxShadow: "0 -8px 32px rgba(0,0,0,0.35)",
          maxHeight: "80vh",
          overflowY: "auto",
          animation: reduced ? undefined : "bottomSheetPanelIn 250ms cubic-bezier(0.32,0.72,0,1)",
          outline: "none",
        }}
      >
        {title && (
          <div
            className="bottom-sheet-header"
            style={{
              alignItems: "center",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              justifyContent: "space-between",
              padding: "16px 20px 12px",
            }}
          >
            <h2
              id={titleId}
              style={{
                color: "var(--text-primary)",
                fontSize: 16,
                fontWeight: 600,
                margin: 0,
              }}
            >
              {title}
            </h2>
            <button
              aria-label="Close"
              className="bottom-sheet-close"
              onClick={onClose}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--text-secondary)",
                cursor: "pointer",
                fontSize: 20,
                lineHeight: 1,
                minHeight: 44,
                minWidth: 44,
                padding: 8,
              }}
              type="button"
            >
              ×
            </button>
          </div>
        )}
        <div className="bottom-sheet-panel" style={{ padding: "16px 20px 24px" }}>
          {children}
        </div>
      </div>
    </>
  );
}
