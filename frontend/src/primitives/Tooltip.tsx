/**
 * Tooltip primitive — accessible hover/focus tooltip (issue #801, part of #796).
 *
 * Wraps a single interactive child and shows a short description on hover OR
 * keyboard focus (accessibility parity), after a small delay. The tooltip is
 * associated with the trigger via `aria-describedby` so screen readers announce
 * it. It dismisses on mouseleave, blur, or Escape.
 *
 * Composition contract (LoD): the child keeps its own props and event handlers
 * — Tooltip chains onto them rather than replacing them. A blank `content`
 * renders no tooltip (so callers can pass through a possibly-empty description
 * without guarding).
 *
 * Reusable: this is the single tooltip used by every nav item and action
 * button across the shell (DRY — see #802's a11y audit).
 */
import React, {
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
} from "react";

export type TooltipPlacement = "top" | "bottom" | "left" | "right";

export interface TooltipProps {
  /** The text shown in the tooltip. Blank → no tooltip is rendered. */
  content: string;
  /** Hover/focus open delay in milliseconds (default 250). */
  delayMs?: number;
  /** Preferred placement relative to the trigger (default "top"). */
  placement?: TooltipPlacement;
  /** Exactly one interactive element to attach the tooltip to. */
  children: ReactElement;
}

interface TriggerLikeProps {
  onMouseEnter?: (e: React.MouseEvent) => void;
  onMouseLeave?: (e: React.MouseEvent) => void;
  onFocus?: (e: React.FocusEvent) => void;
  onBlur?: (e: React.FocusEvent) => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  "aria-describedby"?: string;
}

function placementStyle(placement: TooltipPlacement): React.CSSProperties {
  switch (placement) {
    case "bottom":
      return { top: "calc(100% + 6px)", left: "50%", transform: "translateX(-50%)" };
    case "left":
      return { right: "calc(100% + 6px)", top: "50%", transform: "translateY(-50%)" };
    case "right":
      return { left: "calc(100% + 6px)", top: "50%", transform: "translateY(-50%)" };
    case "top":
    default:
      return { bottom: "calc(100% + 6px)", left: "50%", transform: "translateX(-50%)" };
  }
}

export function Tooltip({
  content,
  delayMs = 250,
  placement = "top",
  children,
}: TooltipProps): ReactElement {
  const id = useId();
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hasContent = content.trim().length > 0;

  const clearTimer = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const show = useCallback(() => {
    if (!hasContent) return;
    clearTimer();
    if (delayMs <= 0) {
      setOpen(true);
      return;
    }
    timer.current = setTimeout(() => setOpen(true), delayMs);
  }, [hasContent, delayMs, clearTimer]);

  const hide = useCallback(() => {
    clearTimer();
    setOpen(false);
  }, [clearTimer]);

  useEffect(() => clearTimer, [clearTimer]);

  const childProps = children.props as TriggerLikeProps;

  const handleMouseEnter = useCallback(
    (e: React.MouseEvent) => {
      childProps.onMouseEnter?.(e);
      show();
    },
    [childProps, show],
  );
  const handleMouseLeave = useCallback(
    (e: React.MouseEvent) => {
      childProps.onMouseLeave?.(e);
      hide();
    },
    [childProps, hide],
  );
  const handleFocus = useCallback(
    (e: React.FocusEvent) => {
      childProps.onFocus?.(e);
      show();
    },
    [childProps, show],
  );
  const handleBlur = useCallback(
    (e: React.FocusEvent) => {
      childProps.onBlur?.(e);
      hide();
    },
    [childProps, hide],
  );
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      childProps.onKeyDown?.(e);
      if (e.key === "Escape") hide();
    },
    [childProps, hide],
  );

  const describedBy = open && hasContent ? id : childProps["aria-describedby"];

  const trigger = cloneElement(children, {
    onMouseEnter: handleMouseEnter,
    onMouseLeave: handleMouseLeave,
    onFocus: handleFocus,
    onBlur: handleBlur,
    onKeyDown: handleKeyDown,
    "aria-describedby": describedBy,
  } as TriggerLikeProps);

  return (
    <span style={{ position: "relative", display: "inline-flex" }}>
      {trigger}
      {open && hasContent && (
        <span
          role="tooltip"
          id={id}
          style={{
            position: "absolute",
            zIndex: 10000,
            ...placementStyle(placement),
            background: "var(--bg-tertiary, #1c2333)",
            color: "var(--text-primary, #e6edf3)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: 6,
            padding: "4px 8px",
            fontSize: 12,
            lineHeight: 1.4,
            whiteSpace: "normal",
            maxWidth: 240,
            width: "max-content",
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            pointerEvents: "none",
          }}
        >
          {content}
        </span>
      )}
    </span>
  );
}
