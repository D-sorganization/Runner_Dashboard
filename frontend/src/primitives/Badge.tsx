import type { HTMLAttributes, ReactNode } from "react";

export type BadgeTone = "success" | "warning" | "danger" | "info" | "neutral";
export type BadgeSize = "sm" | "md";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  children: ReactNode;
  size?: BadgeSize;
  tone?: BadgeTone;
}

const sizeStyles: Record<BadgeSize, { fontSize: string; padding: string }> = {
  sm: { fontSize: "10px", padding: "1px 6px" },
  md: { fontSize: "11px", padding: "2px 8px" },
};

export function Badge({
  children,
  className = "",
  size = "md",
  tone = "neutral",
  style,
  ...props
}: BadgeProps) {
  const sizing = sizeStyles[size];
  const classes = ["badge", `badge-tone-${tone}`, `badge-size-${size}`, className]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      {...props}
      className={classes}
      data-touch-primitive="Badge"
      style={{
        background: `var(--badge-${tone}-bg)`,
        borderRadius: "10px",
        color: `var(--badge-${tone}-fg)`,
        display: "inline-block",
        fontSize: sizing.fontSize,
        fontWeight: 500,
        padding: sizing.padding,
        textTransform: "capitalize",
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {children}
    </span>
  );
}
