import type { HTMLAttributes, ReactNode } from "react";

export type BadgeTone = "success" | "warning" | "danger" | "info" | "neutral";
export type BadgeSize = "sm" | "md";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  children: ReactNode;
  size?: BadgeSize;
  tone?: BadgeTone;
}

export function Badge({
  children,
  className = "",
  size = "md",
  tone = "neutral",
  style,
  ...props
}: BadgeProps) {
  const classes = ["badge", `badge-tone-${tone}`, `badge-size-${size}`, className]
    .filter(Boolean)
    .join(" ");

  return (
    <span
      {...props}
      className={classes}
      data-touch-primitive="Badge"
      style={style}
    >
      {children}
    </span>
  );
}
