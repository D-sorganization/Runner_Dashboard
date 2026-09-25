/**
 * PriorityBadge — the owner's tier for a repository (Repository_Management
 * config/project_priorities.yaml). The focus line, when set, is the tooltip.
 */
import React from "react";
import { Badge } from "../../primitives/Badge";
import type { BadgeTone } from "../../primitives/Badge";
import type { PriorityTier, ProjectPriority } from "./types";

const TIER_TONE: Record<PriorityTier, BadgeTone> = {
  P0: "danger",
  P1: "warning",
  P2: "info",
  P3: "neutral",
  P4: "neutral",
  unranked: "neutral",
};

export function PriorityBadge({
  priority,
}: {
  priority?: ProjectPriority;
}): React.ReactElement {
  const tier = priority?.tier ?? "unranked";
  return (
    <Badge
      tone={TIER_TONE[tier]}
      size="sm"
      aria-label={`Priority ${tier}`}
      title={priority?.focus || undefined}
    >
      {tier}
    </Badge>
  );
}
