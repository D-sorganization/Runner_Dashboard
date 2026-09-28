import React, { useCallback } from "react";
import { Dropdown } from "../primitives/Dropdown";
import { useDensity, type Density } from "../hooks/useDensity";

function DensityIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <line x1="2" y1="3" x2="14" y2="3" />
      <line x1="2" y1="8" x2="14" y2="8" />
      <line x1="2" y1="13" x2="14" y2="13" />
    </svg>
  );
}

export function ShellDensityControl(): React.ReactElement {
  const { density, setDensity } = useDensity();
  const isCompact = density === "compact";

  const handleSelect = useCallback(
    (next: Density) => {
      setDensity(next);
    },
    [setDensity],
  );

  return (
    <div id="density-toggle" data-testid="density-toggle" style={{ display: "inline-flex" }}>
      <Dropdown
        label={isCompact ? "Compact" : "Comfortable"}
        tooltip={`Density: ${isCompact ? "Compact" : "Comfortable"}`}
        Icon={DensityIcon}
        hideLabel
        triggerClassName="shell-action shell-icon-btn"
        items={[
          {
            id: "density-comfortable",
            label: "Comfortable",
            active: !isCompact,
            onSelect: () => handleSelect("comfortable"),
          },
          {
            id: "density-compact",
            label: "Compact",
            active: isCompact,
            onSelect: () => handleSelect("compact"),
          },
        ]}
      />
    </div>
  );
}
