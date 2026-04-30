import type { ButtonHTMLAttributes, ReactNode } from "react";

export interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  selected?: boolean;
}

export function Pill({
  children,
  className = "",
  onClick,
  selected = false,
  style,
  type = "button",
  ...props
}: PillProps) {
  const classes = ["pill", selected ? "pill-selected" : "", className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...props}
      aria-pressed={selected}
      className={classes}
      data-touch-primitive="Pill"
      onClick={onClick}
      style={{
        alignItems: "center",
        background: selected ? "var(--badge-info-bg)" : "var(--bg-tertiary)",
        border: `1px solid ${selected ? "var(--accent-blue)" : "transparent"}`,
        borderRadius: "9999px",
        color: selected ? "var(--badge-info-fg)" : "var(--text-secondary)",
        cursor: onClick ? "pointer" : "default",
        display: "inline-flex",
        fontSize: "12px",
        fontWeight: 600,
        gap: "6px",
        minHeight: "30px",
        padding: "6px 12px",
        textAlign: "center",
        touchAction: "manipulation",
        userSelect: "none",
        whiteSpace: "nowrap",
        ...style,
      }}
      type={type}
    >
      {children}
    </button>
  );
}
