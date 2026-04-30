import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties, ReactNode } from "react";

/**
 * Toaster — global accessible toast notification primitive (issue #421).
 *
 * Mounts at the application root and exposes a `useToast()` hook that any
 * component can consume to surface success / error / info / warning messages.
 *
 * Accessibility contract:
 *   - Non-critical toasts (info, success, warning) render inside a container
 *     with `role="status"` and `aria-live="polite"` so screen readers announce
 *     them after current speech finishes.
 *   - Critical toasts (`error` or any toast with `assertive: true`) render
 *     inside `role="alert"` with `aria-live="assertive"` so they interrupt.
 *   - The Escape key dismisses the topmost (most recent) visible toast.
 *
 * Behaviour:
 *   - Up to 4 toasts are visible at once. Older toasts are FIFO-evicted when
 *     a new toast arrives that would exceed the cap.
 *   - Auto-dismiss durations default per severity: info 5s, success 4s,
 *     warning 8s, error 10s. Callers may override `durationMs` per toast or
 *     pass `durationMs: 0` to disable auto-dismiss.
 */

export type ToastVariant = "info" | "success" | "warning" | "error";

export interface ToastOptions {
  variant?: ToastVariant;
  title?: string;
  durationMs?: number;
  assertive?: boolean;
}

export interface ToastRecord extends Required<Omit<ToastOptions, "title">> {
  id: number;
  message: string;
  title: string;
  createdAt: number;
}

export interface ToastApi {
  showToast: (message: string, options?: ToastOptions) => number;
  dismiss: (id?: number) => void;
}

const MAX_VISIBLE_TOASTS = 4;

const DEFAULT_DURATION_MS: Record<ToastVariant, number> = {
  info: 5000,
  success: 4000,
  warning: 8000,
  error: 10000,
};

const VARIANT_COLORS: Record<ToastVariant, { border: string; accent: string }> = {
  info: { border: "var(--accent-blue)", accent: "var(--accent-blue)" },
  success: { border: "var(--accent-green)", accent: "var(--accent-green)" },
  warning: { border: "var(--accent-yellow)", accent: "var(--accent-yellow)" },
  error: { border: "var(--accent-red)", accent: "var(--accent-red)" },
};

const ToastContext = createContext<ToastApi | null>(null);

/**
 * useToast — React hook returning the toast API. Safe to call before the
 * provider mounts: in that case it logs a console warning and the helpers
 * become no-ops, so consumer modules cannot crash the app at import time.
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx) return ctx;
  return {
    showToast: (message: string) => {
      // eslint-disable-next-line no-console
      console.warn("[Toaster] useToast() called outside <Toaster />:", message);
      return -1;
    },
    dismiss: () => {
      /* no-op when provider is absent */
    },
  };
}

interface ToasterProps {
  children?: ReactNode;
}

/**
 * <Toaster /> — mount once at the app root. Renders two stacked live regions
 * (polite + assertive) and provides the `useToast()` context to descendants.
 * If rendered without children, it still functions as a global mount point;
 * `useToast()` walks up via React context regardless of subtree position.
 */
export function Toaster({ children }: ToasterProps = {}) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const idRef = useRef(0);
  const timersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());

  const dismiss = useCallback((id?: number) => {
    setToasts((current) => {
      if (current.length === 0) return current;
      if (id === undefined) {
        const target = current[current.length - 1];
        const timer = timersRef.current.get(target.id);
        if (timer) clearTimeout(timer);
        timersRef.current.delete(target.id);
        return current.slice(0, -1);
      }
      const timer = timersRef.current.get(id);
      if (timer) clearTimeout(timer);
      timersRef.current.delete(id);
      return current.filter((toast) => toast.id !== id);
    });
  }, []);

  const showToast = useCallback(
    (message: string, options: ToastOptions = {}) => {
      const variant: ToastVariant = options.variant ?? "info";
      const duration =
        options.durationMs === undefined
          ? DEFAULT_DURATION_MS[variant]
          : Math.max(0, options.durationMs);
      const assertive = options.assertive ?? variant === "error";
      idRef.current += 1;
      const id = idRef.current;
      const record: ToastRecord = {
        id,
        message,
        title: options.title ?? "",
        variant,
        durationMs: duration,
        assertive,
        createdAt: Date.now(),
      };
      setToasts((current) => {
        const next = [...current, record];
        while (next.length > MAX_VISIBLE_TOASTS) {
          const evicted = next.shift();
          if (evicted) {
            const timer = timersRef.current.get(evicted.id);
            if (timer) clearTimeout(timer);
            timersRef.current.delete(evicted.id);
          }
        }
        return next;
      });
      if (duration > 0) {
        const timer = setTimeout(() => dismiss(id), duration);
        timersRef.current.set(id, timer);
      }
      return id;
    },
    [dismiss],
  );

  // Escape key dismisses the topmost visible toast.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setToasts((current) => {
          if (current.length === 0) return current;
          const target = current[current.length - 1];
          const timer = timersRef.current.get(target.id);
          if (timer) clearTimeout(timer);
          timersRef.current.delete(target.id);
          return current.slice(0, -1);
        });
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Cleanup timers on unmount so we never fire setState after teardown.
  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(() => ({ showToast, dismiss }), [showToast, dismiss]);

  // Expose the toast API on `window.__toaster` so the legacy fetch wrapper
  // (which lives outside the React tree) can surface 401s and other global
  // events as toasts without importing React state.
  useEffect(() => {
    if (typeof window === "undefined") return;
    (window as unknown as { __toaster?: ToastApi }).__toaster = api;
    return () => {
      const w = window as unknown as { __toaster?: ToastApi };
      if (w.__toaster === api) delete w.__toaster;
    };
  }, [api]);

  const polite = toasts.filter((toast) => !toast.assertive);
  const assertive = toasts.filter((toast) => toast.assertive);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        aria-label="Notifications"
        className="toaster-region toaster-region-polite"
        data-touch-primitive="Toaster"
        style={toasterRegionStyle}
      >
        <div
          aria-live="polite"
          aria-atomic="false"
          role="status"
          className="toaster-live toaster-live-polite"
          style={toasterStackStyle}
        >
          {polite.map((toast) => (
            <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </div>
        <div
          aria-live="assertive"
          aria-atomic="true"
          role="alert"
          className="toaster-live toaster-live-assertive"
          style={toasterStackStyle}
        >
          {assertive.map((toast) => (
            <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

interface ToastItemProps {
  toast: ToastRecord;
  onDismiss: (id: number) => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps) {
  const palette = VARIANT_COLORS[toast.variant];
  return (
    <div
      className={`toaster-toast toaster-toast-${toast.variant}`}
      data-toast-id={toast.id}
      data-toast-variant={toast.variant}
      style={{
        ...toastItemStyle,
        borderLeftColor: palette.border,
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        {toast.title ? (
          <div
            className="toaster-toast-title"
            style={{ color: palette.accent, fontWeight: 600, marginBottom: 2 }}
          >
            {toast.title}
          </div>
        ) : null}
        <div className="toaster-toast-message" style={{ color: "var(--text-primary)" }}>
          {toast.message}
        </div>
      </div>
      <button
        aria-label="Dismiss notification"
        className="toaster-toast-dismiss"
        onClick={() => onDismiss(toast.id)}
        style={dismissButtonStyle}
        type="button"
      >
        ×
      </button>
    </div>
  );
}

const toasterRegionStyle: CSSProperties = {
  position: "fixed",
  bottom: "calc(var(--bottom-nav-height, 0px) + 16px)",
  right: 16,
  left: 16,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  pointerEvents: "none",
  zIndex: 10000,
};

const toasterStackStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  pointerEvents: "none",
};

const toastItemStyle: CSSProperties = {
  pointerEvents: "auto",
  display: "flex",
  alignItems: "flex-start",
  gap: 12,
  padding: "12px 14px",
  background: "var(--bg-secondary)",
  color: "var(--text-primary)",
  border: "1px solid var(--border)",
  borderLeft: "4px solid var(--accent-blue)",
  borderRadius: 8,
  boxShadow: "var(--glass-shadow, 0 8px 32px 0 rgba(0,0,0,0.37))",
  fontSize: 14,
  lineHeight: 1.4,
  maxWidth: "min(480px, 100%)",
  marginLeft: "auto",
};

const dismissButtonStyle: CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--text-secondary)",
  cursor: "pointer",
  fontSize: 18,
  lineHeight: 1,
  padding: 4,
  minWidth: 32,
  minHeight: 32,
};
