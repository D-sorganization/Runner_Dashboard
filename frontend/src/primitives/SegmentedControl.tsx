import type { KeyboardEvent } from "react";

export interface SegmentedControlOption {
  disabled?: boolean;
  label: string;
  value: string;
}

export interface SegmentedControlProps {
  ariaLabel: string;
  onChange: (value: string) => void;
  options: SegmentedControlOption[];
  value: string;
}

export function SegmentedControl({
  ariaLabel,
  onChange,
  options,
  value,
}: SegmentedControlProps) {
  function selectNext(delta: number) {
    const enabled = options.filter((option) => !option.disabled);
    const current = enabled.findIndex((option) => option.value === value);
    if (current < 0 || enabled.length === 0) return;
    const next = enabled[(current + delta + enabled.length) % enabled.length];
    onChange(next.value);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      selectNext(1);
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      selectNext(-1);
    }
  }

  return (
    <div
      aria-label={ariaLabel}
      className="segmented-control"
      data-touch-primitive="SegmentedControl"
      onKeyDown={onKeyDown}
      role="radiogroup"
    >
      {options.map((option) => (
        <button
          aria-checked={option.value === value}
          className={
            "segmented-control-option" +
            (option.value === value ? " segmented-control-option-active" : "")
          }
          disabled={option.disabled}
          key={option.value}
          onClick={() => onChange(option.value)}
          role="radio"
          tabIndex={option.value === value ? 0 : -1}
          type="button"
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
