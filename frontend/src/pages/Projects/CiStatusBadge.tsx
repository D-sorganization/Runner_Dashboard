/**
 * CiStatusBadge — a repository's latest CI result on its Projects card
 * (#1338, SC-G6: folded in from the retired Organization tab). Data comes
 * from `GET /api/repos` via the page; `undefined` means the repo is not in
 * that list or the list could not be loaded.
 */
import React from "react";
import { Badge, type BadgeTone } from "../../primitives/Badge";
import type { RepoCiStatus } from "./types";

const TONES: Record<string, BadgeTone> = {
  success: "success",
  failure: "danger",
  timed_out: "danger",
  startup_failure: "danger",
  cancelled: "warning",
  action_required: "warning",
  in_progress: "info",
  queued: "info",
  waiting: "info",
  pending: "info",
};

export function CiStatusBadge({ ci }: { ci?: RepoCiStatus }): React.ReactElement {
  if (!ci) {
    return (
      <Badge tone="neutral" size="sm">
        CI unknown
      </Badge>
    );
  }
  const state = ci.conclusion || ci.status;
  if (!state) {
    return (
      <Badge tone="neutral" size="sm">
        No CI
      </Badge>
    );
  }
  const label = `CI ${state}`;
  const badge = (
    <Badge tone={TONES[state] ?? "neutral"} size="sm">
      {label}
    </Badge>
  );
  if (!ci.runUrl) return badge;
  return (
    <a href={ci.runUrl} target="_blank" rel="noreferrer" aria-label={label}>
      {badge}
    </a>
  );
}
