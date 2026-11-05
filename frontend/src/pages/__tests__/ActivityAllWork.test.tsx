// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { conflictLabelsForEvent } from "../Activity/activityContextApi";
import type { ActivityEnvelope } from "../Activity/activityApi";
import { eventMatchesFilters } from "../Activity/activityUrlState";

const EVENT: ActivityEnvelope = {
  schema_version: 1,
  event_id: "e1",
  source: "staff_run",
  principal: "cursor-a",
  node: "node-a",
  session_id: "sess-a",
  seq: 1,
  ts: "2026-10-28T18:00:00Z",
  repo: "Runner_Dashboard",
  issue: 2865,
  branch: null,
  pr: null,
  kind: "heartbeat",
  redaction_version: 1,
  payload: { agent: "cursor" },
};

describe("activityUrlState", () => {
  it("filters events by repo, issue, agent, and node", () => {
    expect(eventMatchesFilters(EVENT, { repo: "Runner_Dashboard", issue: "2865", agent: "cursor", node: "node-a" })).toBe(
      true,
    );
    expect(eventMatchesFilters(EVENT, { repo: "Other", issue: "", agent: "", node: "" })).toBe(false);
    expect(eventMatchesFilters(EVENT, { repo: "", issue: "999", agent: "", node: "" })).toBe(false);
  });
});

describe("conflictLabelsForEvent", () => {
  it("derives badges only from C1 conflict summaries", () => {
    const labels = conflictLabelsForEvent(EVENT, [
      {
        conflict_id: "c1",
        kind: "claim_conflict",
        repo: "Runner_Dashboard",
        issue: 2865,
        sessions: ["sess-a"],
        agents: ["cursor"],
        branches: [],
        paths: [],
        goals: [],
        detected_at: "2026-10-28T18:00:00Z",
      },
    ]);
    expect(labels).toEqual(["claim conflict"]);
  });
});
