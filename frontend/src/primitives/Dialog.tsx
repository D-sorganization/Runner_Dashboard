/**
 * Dialog primitive — a11y-compliant modal dialog (issue #371).
 *
 * Features:
 * - role="dialog", aria-modal="true", aria-labelledby
 * - Focus trap: Tab / Shift+Tab cycle within the dialog
 * - Focus moves to first interactive element on open; returns to trigger on close
 * - Escape key closes the dialog
 * - Outside-click closes (configurable via closeOnOverlayClick)
 * - inert attribute applied to sibling DOM trees while open
 * - Reduced-motion: animation disabled when prefers-reduced-motion is set
 */
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
} from "react";

// ── Focusable element selector ────────────────────────────────────────────────
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

// ── Context ───────────────────────────────────────────────────────────────────
interface DialogContextValue {
  titleId: string;
  onClose: () => void;
}

const DialogContext = createContext<DialogContextValue | null>(null);

function useDialogContext(componentName: string): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) {
    throw new Error(`<${componentName}> must be rendered inside <Dialog>`);
  }
  return ctx;
}

// ── Types ─────────────────────────────────────────────────────────────────────
export interface DialogProps {
  /** Whether the dialog is visible. */
  open: boolean;
  /** Called when the dialog requests to be closed (Escape, overlay click, close button). */
  onClose: () => void;
  children: React.ReactNode;
  /** Optional id of content that describes the dialog. */
  ariaDescribedBy?: string;
  /** When true (default), clicking the overlay backdrop closes the dialog. */
  closeOnOverlayClick?: boolean;
  /** Optional extra CSS class for the panel container. */
  className?: string;
}

export interface DialogTitleProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}

export interface DialogContentProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}

export interface DialogActionsProps {
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}

export interface DialogCloseProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  children?: React.ReactNode;
}

// ── Dialog (root) ─────────────────────────────────────────────────────────────
export function Dialog({
  open,
  onClose,
  children,
  ariaDescribedBy,
  closeOnOverlayClick = true,
  className,
}: DialogProps): React.ReactElement | null {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<Element | null>(null);
  const reduced = prefersReducedMotion();

  // Remember the element that had focus before the dialog opened so we can
  // restore focus when it closes.
  useEffect(() => {
    if (open) {
      triggerRef.current = document.activeElement;
    }
  }, [open]);

  // Focus first interactive element when dialog opens.
  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;

    const focusable = getFocusable(panel);
    if (focusable.length > 0) {
      focusable[0].focus();
    } else {
      panel.focus();
    }
  }, [open]);

  // Restore focus to trigger when dialog closes.
  useEffect(() => {
    if (!open && triggerRef.current instanceof HTMLElement) {
      triggerRef.current.focus();
      triggerRef.current = null;
    }
  }, [open]);

  // Apply inert to all siblings of the portal root while the dialog is open.
  useEffect(() => {
    if (!open) return;
    const root = document.getElementById("root");
    if (!root) return;

    const siblings = Array.from(document.body.children).filter(
      (el) => el !== root && el.tagName !== "SCRIPT" && el.tagName !== "LINK",
    );

    siblings.forEach((el) => {
      (el as HTMLElement).inert = true;
    });

    return () => {
      siblings.forEach((el) => {
        (el as HTMLElement).inert = false;
      });
    };
  }, [open]);

  // Escape key — close the dialog.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", handler, true);
    return () => document.removeEventListener("keydown", handler, true);
  }, [open, onClose]);

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

  const handleOverlayClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (closeOnOverlayClick && e.target === e.currentTarget) {
        onClose();
      }
    },
    [closeOnOverlayClick, onClose],
  );

  if (!open) return null;

  return (
    <DialogContext.Provider value={{ titleId, onClose }}>
      {/* Overlay / backdrop */}
      <div
        aria-hidden="true"
        onClick={handleOverlayClick}
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15,17,23,0.7)",
          zIndex: 9998,
          animation: reduced ? undefined : "dialogOverlayIn 150ms ease",
        }}
      />
      {/* Panel */}
      <div
        ref={panelRef}
        aria-describedby={ariaDescribedBy}
        aria-labelledby={titleId}
        aria-modal="true"
        className={className}
        onKeyDown={handleKeyDown}
        role="dialog"
        tabIndex={-1}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 9999,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 16,
          pointerEvents: "none",
        }}
      >
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            boxShadow: "0 16px 48px rgba(0,0,0,0.35)",
            maxWidth: 520,
            width: "100%",
            maxHeight: "85vh",
            overflowY: "auto",
            padding: 24,
            pointerEvents: "auto",
            animation: reduced ? undefined : "dialogPanelIn 150ms ease",
          }}
        >
          {children}
        </div>
      </div>
    </DialogContext.Provider>
  );
}

// ── DialogTitle ───────────────────────────────────────────────────────────────
export function DialogTitle({
  children,
  style,
  className,
}: DialogTitleProps): React.ReactElement {
  const { titleId } = useDialogContext("DialogTitle");
  return (
    <h2
      id={titleId}
      className={className}
      style={{
        margin: "0 0 12px 0",
        fontSize: 16,
        fontWeight: 600,
        color: "var(--text-primary)",
        ...style,
      }}
    >
      {children}
    </h2>
  );
}

// ── DialogContent ─────────────────────────────────────────────────────────────
export function DialogContent({
  children,
  style,
  className,
}: DialogContentProps): React.ReactElement {
  return (
    <div
      className={className}
      style={{ color: "var(--text-secondary)", fontSize: 14, ...style }}
    >
      {children}
    </div>
  );
}

// ── DialogActions ─────────────────────────────────────────────────────────────
export function DialogActions({
  children,
  style,
  className,
}: DialogActionsProps): React.ReactElement {
  return (
    <div
      className={className}
      style={{
        display: "flex",
        justifyContent: "flex-end",
        gap: 8,
        marginTop: 20,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

// ── DialogClose ───────────────────────────────────────────────────────────────
export function DialogClose({
  children = "Close",
  onClick,
  ...rest
}: DialogCloseProps): React.ReactElement {
  const { onClose } = useDialogContext("DialogClose");
  return (
    <button
      type="button"
      onClick={(e) => {
        onClose();
        onClick?.(e);
      }}
      style={{
        padding: "8px 14px",
        fontSize: 13,
        borderRadius: 6,
        border: "1px solid var(--border)",
        background: "var(--bg-primary)",
        color: "var(--text-primary)",
        cursor: "pointer",
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
