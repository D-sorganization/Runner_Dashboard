/**
 * FleetSummaryBar — the fleet-wide status line above the project cards:
 * charter coverage, feature counts, repos per priority tier, decisions waiting
 * on the owner and untracked open work. Read-only; data from GET /api/projects.
 */
import React from "react";
import type { FleetSummary, PriorityTier } from "./types";

const TIER_ORDER: PriorityTier[] = ["P0", "P1", "P2", "P3", "P4", "unranked"];

export function FleetSummaryBar({
  summary,
  prioritiesError,
}: {
  summary: FleetSummary;
  prioritiesError?: string;
}): React.ReactElement {
  const f = summary.features;
  return (
    <section
      className="section"
      data-testid="fleet-summary"
      aria-label="Fleet summary"
      style={{ display: "flex", flexWrap: "wrap", gap: 16, fontSize: 13 }}
    >
      <span>
        {summary.with_charter} / {summary.repos} repos chartered
      </span>
      <span>
        {f.shipped} shipped · {f.in_progress} in progress · {f.planned} planned
        · {f.parked} parked
      </span>
      <span>
        {TIER_ORDER.map((tier) => (
          <span key={tier} style={{ marginRight: 8 }}>
            {tier}: {summary.by_tier[tier] ?? 0}
          </span>
        ))}
      </span>
      <span>{summary.decisions_needed} decisions needed</span>
      <span>{summary.untracked_items} untracked open items</span>
      {prioritiesError && (
        <span style={{ color: "var(--accent-orange, var(--text-secondary))" }}>
          Priorities unavailable ({prioritiesError}); all repos shown unranked.
        </span>
      )}
    </section>
  );
}
