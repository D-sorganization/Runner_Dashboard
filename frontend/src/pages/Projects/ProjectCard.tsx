/**
 * ProjectCard — one repository on the Projects tab (issue #1199): feature
 * progress, decisions needed, last project-steward run and a "Run steward now"
 * action. Presentational; the POST is owned by the page so the card stays pure.
 */
import React from "react";
import { Badge } from "../../primitives/Badge";
import { TimeAgo } from "../../primitives/TimeAgo";
import { FeatureProgressBar } from "./FeatureProgressBar";
import { FeatureDetails } from "./FeatureDetails";
import type { ProjectOverview, StewardRun } from "./types";

export interface ProjectCardProps {
  project: ProjectOverview;
  /** True while a steward run is being submitted for this repo. */
  running: boolean;
  /** Result line from the last "Run steward now" click, if any. */
  notice?: string;
  onRunSteward: (repo: string) => void;
}

function runTone(
  status: string,
): "success" | "warning" | "danger" | "info" | "neutral" {
  if (status === "succeeded") return "success";
  if (status === "failed" || status === "blocked") return "danger";
  if (status === "running" || status === "queued" || status === "preparing")
    return "info";
  if (status === "cancelled") return "warning";
  return "neutral";
}

function StewardRunLine({
  run,
}: {
  run: StewardRun | null;
}): React.ReactElement {
  if (!run) {
    return (
      <span style={{ color: "var(--text-secondary)" }}>
        No steward run recorded on this node.
      </span>
    );
  }
  // No Staff tab exists on this branch yet (#1198); link to the run JSON.
  return (
    <span>
      <Badge tone={runTone(run.status)} size="sm">
        {run.status}
      </Badge>{" "}
      <a
        href={`/api/staff/runs/${encodeURIComponent(run.id)}`}
        target="_blank"
        rel="noreferrer"
      >
        run {run.id.slice(0, 8)}
      </a>{" "}
      <TimeAgo iso={run.ended_at || run.created_at} />
      {run.machine ? ` on ${run.machine}` : ""}
    </span>
  );
}

export function ProjectCard({
  project,
  running,
  notice,
  onRunSteward,
}: ProjectCardProps): React.ReactElement {
  const {
    repo,
    charter_present,
    features,
    progress,
    decisions_needed,
    last_steward_run,
    error,
  } = project;
  return (
    <article
      className="section projects-card"
      aria-label={`Project ${repo}`}
      data-testid={`project-card-${repo}`}
    >
      <div
        className="section-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h3 style={{ margin: 0 }}>{repo}</h3>
        <button
          type="button"
          className="btn btn-blue"
          disabled={running}
          onClick={() => onRunSteward(repo)}
          aria-label={`Run steward now for ${repo}`}
        >
          {running ? "Submitting…" : "Run steward now"}
        </button>
      </div>
      {error && (
        <p role="alert" style={{ color: "var(--accent-red)", fontSize: 13 }}>
          {error}
        </p>
      )}
      {charter_present ? (
        <FeatureProgressBar progress={progress} />
      ) : (
        <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          No <code>docs/project/CHARTER.md</code> yet — the steward will draft
          one on its first pass.
        </p>
      )}
      <FeatureDetails features={features} />
      <div style={{ marginTop: 8, fontSize: 13 }}>
        <strong>Decisions needed</strong>
        {decisions_needed.length === 0 ? (
          <span style={{ color: "var(--text-secondary)" }}> — none</span>
        ) : (
          <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
            {decisions_needed.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        )}
      </div>
      <div style={{ marginTop: 8, fontSize: 13 }}>
        <strong>Last steward run</strong>{" "}
        <StewardRunLine run={last_steward_run} />
      </div>
      {notice && (
        <p
          role="status"
          style={{ marginTop: 6, fontSize: 12, color: "var(--text-secondary)" }}
        >
          {notice}
        </p>
      )}
    </article>
  );
}
