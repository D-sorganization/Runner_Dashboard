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
        borderRadius: "var(--radius-pill, 9999px)",
        color: selected ? "var(--badge-info-fg)" : "var(--text-secondary)",
        cursor: onClick ? "pointer" : "default",
        display: "inline-flex",
        fontSize: "11.5px",
        fontWeight: 500,
        gap: "4px",
        height: "20px",
        minHeight: "20px",
        padding: "0 8px",
        textAlign: "center",
        touchAction: "manipulation",
        userSelect: "none",
        whiteSpace: "nowrap",
        lineHeight: 1,
        transition: "background-color 140ms ease-out, color 140ms ease-out, border-color 140ms ease-out",
        ...style,
      }}
      type={type}
    >
      {children}
    </button>
  );
}
