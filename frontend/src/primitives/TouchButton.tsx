import type { ButtonHTMLAttributes, ReactNode } from "react";

export type TouchButtonVariant = "default" | "primary" | "danger";

export interface TouchButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: TouchButtonVariant;
  pressed?: boolean;
}

export function TouchButton({
  children,
  className = "",
  type = "button",
  variant = "default",
  pressed,
  ...props
}: TouchButtonProps) {
  const classes = ["touch-button", `touch-button-${variant}`, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...props}
      aria-pressed={pressed}
      className={classes}
      data-touch-primitive="TouchButton"
      type={type}
    >
      {children}
    </button>
  );
}
