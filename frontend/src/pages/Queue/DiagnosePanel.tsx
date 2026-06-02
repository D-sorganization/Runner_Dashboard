// DiagnosePanel – "Why are jobs waiting?" runner-routing breakdown.
//
// Migrated off the legacy `React.createElement` / `any` pattern (issue #841)
// to typed TSX. DOM structure and styling are preserved.

import type { DiagnosePayload, RunnerGroup, SampledJob } from "./types";

interface DiagnosePanelProps {
  diag: DiagnosePayload;
}

function bottleneckColor(diag: DiagnosePayload): string {
  if (diag.pick_runner_misconfig && diag.pick_runner_misconfig.length > 0) {
    return "var(--accent-red)";
  }
  if (diag.runner_groups_restricted && (diag.waiting_for_generic_self_hosted ?? 0) > 0) {
    return "var(--accent-red)";
  }
  if (
    (diag.waiting_for_self_hosted ?? 0) > 0 &&
    diag.runner_pool &&
    diag.runner_pool.idle === 0
  ) {
    return "var(--accent-yellow)";
  }
  if ((diag.waiting_for_github_hosted ?? 0) > 0) {
    return "var(--accent-blue)";
  }
  return "var(--accent-green)";
}

function targetColor(target: string | undefined): string {
  switch (target) {
    case "self-hosted (d-sorg-fleet)":
      return "var(--accent-yellow)";
    case "self-hosted (generic)":
      return "var(--accent-orange)";
    case "github-hosted":
      return "var(--accent-blue)";
    default:
      return "var(--accent-red)";
  }
}

function RunnerGroupRow({ group }: { group: RunnerGroup }) {
  const hasRunners = (group.runner_count ?? 0) > 0;
  const hasBlocked =
    !!group.blocked_waiting_repos && group.blocked_waiting_repos.length > 0;
  const accent = hasBlocked
    ? "var(--accent-red)"
    : group.restricted
      ? "var(--accent-orange)"
      : "var(--accent-green)";

  return (
    <div
      style={{
        marginBottom: 6,
        padding: "8px 10px",
        borderRadius: 6,
        fontSize: 12,
        border:
          "1px solid " +
          (hasBlocked
            ? "var(--accent-red)"
            : group.restricted
              ? "var(--accent-orange)"
              : "var(--border)"),
        background: hasBlocked
          ? "rgba(248,81,73,0.08)"
          : group.restricted
            ? "rgba(240,136,62,0.08)"
            : "var(--bg-tertiary)",
      }}
    >
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <b style={{ color: accent }}>{group.name}</b>
        {group.inherited && (
          <span
            style={{
              fontSize: 10,
              padding: "1px 5px",
              borderRadius: 3,
              background: "rgba(88,166,255,0.15)",
              color: "var(--accent-blue)",
              border: "1px solid rgba(88,166,255,0.3)",
              fontWeight: 600,
            }}
          >
            ENTERPRISE
          </span>
        )}
        {!group.inherited && (
          <span
            style={{
              fontSize: 10,
              padding: "1px 5px",
              borderRadius: 3,
              background: "rgba(63,185,80,0.1)",
              color: "var(--accent-green)",
              border: "1px solid rgba(63,185,80,0.3)",
              fontWeight: 600,
            }}
          >
            ORG
          </span>
        )}
        <span style={{ color: "var(--text-muted)" }}>{group.visibility}</span>
        {hasRunners && (
          <span style={{ color: "var(--text-secondary)" }}>
            {group.runner_count}
            {" runner" + (group.runner_count !== 1 ? "s" : "")}
            {group.runner_names && group.runner_names.length > 0
              ? ": " + group.runner_names.join(", ")
              : ""}
          </span>
        )}
        {!hasRunners && (
          <span style={{ color: "var(--text-muted)" }}>no runners</span>
        )}
      </div>
      {hasBlocked && (
        <div style={{ marginTop: 4, color: "var(--accent-red)" }}>
          {group.inherited
            ? "Enterprise group is restricted — these repos cannot access it (or any org runners it gates): "
            : "Blocking repos: "}
          {group.blocked_waiting_repos?.join(", ")}
          {group.inherited
            ? " — fix: change enterprise group visibility to 'All repositories' in GitHub Enterprise settings"
            : " — these jobs cannot reach the runners in this group"}
        </div>
      )}
      {group.restricted &&
        group.allowed_repos &&
        group.allowed_repos.length > 0 &&
        !hasBlocked && (
          <div style={{ marginTop: 4, color: "var(--text-muted)" }}>
            Allowed: {group.allowed_repos.join(", ")}
          </div>
        )}
    </div>
  );
}

function SampledJobRow({ job }: { job: SampledJob }) {
  return (
    <tr>
      <td>{job.repo}</td>
      <td>{job.job}</td>
      <td style={{ color: targetColor(job.target), fontSize: 12 }}>{job.target}</td>
      <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
        {job.labels && job.labels.join(", ")}
      </td>
    </tr>
  );
}

export function DiagnosePanel({ diag }: DiagnosePanelProps) {
  const pool = diag.runner_pool ?? {};
  return (
    <div
      style={{
        marginTop: 10,
        padding: "12px 16px",
        background: "var(--bg-secondary)",
        borderRadius: 8,
        border: "1px solid var(--border)",
      }}
    >
      <div
        style={{
          fontWeight: 600,
          marginBottom: 6,
          color: bottleneckColor(diag),
        }}
      >
        {diag.bottleneck}
      </div>
      <div style={{ display: "flex", gap: 16, marginTop: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Fleet runners — <b>{pool.busy || 0}</b> busy / <b>{pool.idle || 0}</b>{" "}
          idle / <b>{pool.offline || 0}</b> offline
        </span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Waiting (d-sorg-fleet):{" "}
          <b style={{ color: "var(--accent-yellow)" }}>
            {diag.waiting_for_fleet || 0}
          </b>
        </span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Waiting (generic self-hosted):{" "}
          <b
            style={{
              color:
                (diag.waiting_for_generic_self_hosted ?? 0) > 0
                  ? "var(--accent-orange)"
                  : "inherit",
            }}
          >
            {diag.waiting_for_generic_self_hosted || 0}
          </b>
        </span>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
          Waiting (GitHub-hosted):{" "}
          <b style={{ color: "var(--accent-blue)" }}>
            {diag.waiting_for_github_hosted || 0}
          </b>
        </span>
      </div>
      {diag.runner_groups && diag.runner_groups.length > 0 ? (
        <div style={{ marginTop: 10 }}>
          {diag.runner_groups.map((group, i) => (
            <RunnerGroupRow key={i} group={group} />
          ))}
        </div>
      ) : null}
      {diag.sampled_jobs && diag.sampled_jobs.length > 0 ? (
        <details style={{ marginTop: 8 }}>
          <summary
            style={{
              fontSize: 12,
              cursor: "pointer",
              color: "var(--text-secondary)",
            }}
          >
            Job details ({diag.jobs_sampled} sampled)
          </summary>
          <table className="data-table" style={{ marginTop: 6 }}>
            <thead>
              <tr>
                <th>Repo</th>
                <th>Job</th>
                <th>Target runner</th>
                <th>Labels</th>
              </tr>
            </thead>
            <tbody>
              {(diag.sampled_jobs ?? []).map((job, i) => (
                <SampledJobRow key={i} job={job} />
              ))}
            </tbody>
          </table>
        </details>
      ) : null}
    </div>
  );
}

export default DiagnosePanel;
