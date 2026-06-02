/**
 * Tests.tsx — the "Tests" tab, extracted (behaviour-wise 1:1) from the legacy
 * `App.tsx` monolith as part of the decomposition epic (#836, pass 3).
 *
 * Two sections:
 *  1. CI Tests — latest CI result per fleet repo, with a "Re-run Failed"
 *     trigger (`POST /api/tests/rerun`) for failed/cancelled runs.
 *  2. Heavy Test Suite — per-repo workflow_dispatch / Docker dispatch
 *     (`POST /api/heavy-tests/dispatch` and `/api/heavy-tests/docker`), with a
 *     collapsible recent-runs table.
 *
 * Presentational: the CI results and the heavy-test repo inventory (and their
 * polls) are owned by the legacy App, so this page receives the already-fetched
 * `ciResults` / `testRepos` plus a `loading` flag. The dispatch/rerun POSTs are
 * owned here because they are write-only actions with self-contained per-card
 * state. Loading/empty states and a11y semantics match the original render.
 *
 * The legacy version called `useState` inside a `.map()` (a hooks-rules
 * violation that happened to work because the repo list was stable); the
 * heavy-test card is hoisted into its own `TestRepoCard` component here so each
 * card's hooks are top-level and stable.
 */
import React, { useState } from "react";
import { Collapse } from "../components/Collapse";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { ActivityGlyph, DockerGlyph, FlaskGlyph, PlayGlyph } from "./decompIcons";

function timeAgo(d?: string | null): string {
  if (!d) return "";
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

const CONCLUSION_COLOR: Record<string, string> = {
  success: "var(--accent-green)",
  failure: "var(--accent-red)",
  cancelled: "var(--text-secondary)",
  skipped: "var(--text-secondary)",
  in_progress: "var(--accent-yellow)",
  queued: "var(--accent-yellow)",
};

/** A single CI result row (latest run per repo). */
export interface CiResult {
  repo: string;
  status?: string | null;
  conclusion?: string | null;
  head_branch?: string | null;
  run_number?: number | null;
  run_id?: number | string | null;
  updated_at?: string | null;
  html_url?: string | null;
}

/** A recent heavy-test run for a repo. */
export interface HeavyTestRun {
  id: number | string;
  run_number?: number | null;
  status?: string | null;
  conclusion?: string | null;
  head_branch?: string | null;
  triggering_actor?: string | null;
  updated_at?: string | null;
  html_url?: string | null;
}

/** A repo configured for the heavy (integration) test suite. */
export interface TestRepo {
  name: string;
  description?: string | null;
  default_python: string;
  python_versions: string[];
  recent_runs?: HeavyTestRun[];
}

export interface TestsProps {
  /** Heavy-test repo inventory (owned/polled by the legacy App). */
  testRepos: TestRepo[];
  /** True while the heavy-test repo inventory is loading. */
  loading: boolean;
  /** Latest CI result per fleet repo; defaults to an empty list. */
  ciResults?: CiResult[];
}

type RerunStatus = "running" | "triggered" | "error" | undefined;

interface DispatchEntry {
  status?: string;
  output?: string;
}

// ── CI section ───────────────────────────────────────────────────────────────

function CiSection({ ciResults }: { ciResults: CiResult[] }): React.ReactElement {
  const [rerunState, setRerunState] = useState<Record<string, RerunStatus>>({});

  function rerunFailed(repo: string, runId: number | string | null | undefined): void {
    setRerunState((prev) => ({ ...prev, [repo]: "running" }));
    legacyFetch("/api/tests/rerun", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ repo, run_id: runId }),
    })
      .then((r) => {
        if (!r.ok) throw new Error("rerun failed");
        return r.json();
      })
      .then(() => {
        setRerunState((prev) => ({ ...prev, [repo]: "triggered" }));
      })
      .catch(() => {
        setRerunState((prev) => ({ ...prev, [repo]: "error" }));
      });
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <span style={{ fontWeight: 700, fontSize: 16, color: "var(--text-primary)" }}>
          <ActivityGlyph size={16} /> CI Tests — Fleet Repos
        </span>
      </div>
      {ciResults.length === 0 ? (
        <div style={{ color: "var(--text-secondary)", fontSize: 13, padding: "12px 0" }}>
          {"Loading CI results…"}
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Repo</th>
                <th>Status</th>
                <th>Branch</th>
                <th>Run #</th>
                <th>Updated</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {ciResults.map((r) => {
                const concl = r.conclusion || r.status || "unknown";
                const color = CONCLUSION_COLOR[concl] || "var(--text-secondary)";
                const rerunSt = rerunState[r.repo];
                const canRerun =
                  !!r.run_id &&
                  (r.conclusion === "failure" || r.conclusion === "cancelled");
                return (
                  <tr key={r.repo}>
                    <td>
                      {r.html_url ? (
                        <a
                          href={r.html_url}
                          target="_blank"
                          rel="noopener"
                          style={{ color: "var(--accent-blue)" }}
                        >
                          {r.repo}
                        </a>
                      ) : (
                        r.repo
                      )}
                    </td>
                    <td>
                      <span className={"conclusion-badge " + concl} style={{ color }}>
                        {concl}
                      </span>
                    </td>
                    <td>{r.head_branch || "main"}</td>
                    <td>{r.run_number ? "#" + r.run_number : "—"}</td>
                    <td>{r.updated_at ? new Date(r.updated_at).toLocaleString() : "—"}</td>
                    <td>
                      {canRerun ? (
                        <button
                          className="btn btn-sm btn-blue"
                          type="button"
                          disabled={rerunSt === "running"}
                          onClick={() => rerunFailed(r.repo, r.run_id)}
                        >
                          {rerunSt === "running" ? (
                            <span className="spinner" />
                          ) : (
                            <PlayGlyph size={12} />
                          )}
                          {rerunSt === "triggered" ? " Triggered" : " Re-run Failed"}
                        </button>
                      ) : r.run_id ? (
                        <a
                          href={r.html_url ?? undefined}
                          target="_blank"
                          rel="noopener"
                          className="btn btn-sm"
                          style={{ textDecoration: "none" }}
                        >
                          View
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Heavy-test repo card ─────────────────────────────────────────────────────

function dispatchBadgeClass(status: string | undefined, kind: "github" | "docker"): string {
  if (kind === "github") {
    return status === "dispatched"
      ? "success"
      : status === "error"
        ? "failure"
        : "in_progress";
  }
  return status === "completed"
    ? "success"
    : status === "error" || status === "failed"
      ? "failure"
      : "in_progress";
}

function TestRepoCard({ repo }: { repo: TestRepo }): React.ReactElement {
  const [pyVer, setPyVer] = useState(repo.default_python);
  const [branch, setBranch] = useState("main");
  const [ghState, setGhState] = useState<DispatchEntry>({});
  const [dkState, setDkState] = useState<DispatchEntry>({});

  function dispatch(method: "github" | "docker", py: string, ref: string): void {
    const setState = method === "docker" ? setDkState : setGhState;
    setState({ status: "dispatching", output: "" });
    const url =
      method === "docker" ? "/api/heavy-tests/docker" : "/api/heavy-tests/dispatch";
    legacyFetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ repo: repo.name, python_version: py, ref: ref || "main" }),
    })
      .then((r) => r.json())
      .then((data) => {
        setState({
          status: data.status || "done",
          output: JSON.stringify(data, null, 2),
        });
      })
      .catch((err: Error) => {
        setState({ status: "error", output: err.message });
      });
  }

  return (
    <div className="test-card">
      <div className="test-card-header">
        <div>
          <div className="test-card-title">
            <FlaskGlyph size={20} /> {repo.name}
          </div>
          <div className="test-card-desc">{repo.description}</div>
        </div>
      </div>
      <div className="form-row">
        <span className="form-label">Python Version:</span>
        <select
          className="form-select"
          aria-label={"Python version for " + repo.name}
          value={pyVer}
          onChange={(e) => setPyVer(e.target.value)}
        >
          {repo.python_versions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </div>
      <div className="form-row">
        <span className="form-label">Branch / Ref:</span>
        <input
          className="form-input"
          aria-label={"Branch or ref for " + repo.name}
          value={branch}
          onChange={(e) => setBranch(e.target.value)}
          placeholder="main"
        />
      </div>
      <div className="dispatch-actions">
        <button
          className="btn btn-lg btn-blue"
          type="button"
          disabled={ghState.status === "dispatching"}
          onClick={() => dispatch("github", pyVer, branch)}
        >
          {ghState.status === "dispatching" ? (
            <span className="spinner" />
          ) : (
            <PlayGlyph size={14} />
          )}
          {" Run via GitHub Actions"}
        </button>
        <button
          className="btn btn-lg btn-purple"
          type="button"
          disabled={dkState.status === "dispatching"}
          onClick={() => dispatch("docker", pyVer, branch)}
        >
          {dkState.status === "dispatching" ? (
            <span className="spinner" />
          ) : (
            <DockerGlyph size={14} />
          )}
          {" Run in Docker (Local)"}
        </button>
      </div>
      {ghState.output ? (
        <div>
          <div
            style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 12 }}
          >
            {"GitHub Actions: "}
            <span className={"conclusion-badge " + dispatchBadgeClass(ghState.status, "github")}>
              {ghState.status}
            </span>
          </div>
          <div className="output-box">{ghState.output}</div>
        </div>
      ) : null}
      {dkState.output ? (
        <div>
          <div
            style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 12 }}
          >
            {"Docker: "}
            <span className={"conclusion-badge " + dispatchBadgeClass(dkState.status, "docker")}>
              {dkState.status}
            </span>
          </div>
          <div className="output-box">{dkState.output}</div>
        </div>
      ) : null}
      {repo.recent_runs && repo.recent_runs.length > 0 ? (
        <Collapse
          title="Recent Heavy Test Runs"
          icon={<ActivityGlyph size={14} />}
          badge={repo.recent_runs.length + " runs"}
          defaultOpen={false}
        >
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Status</th>
                  <th>Branch</th>
                  <th>Triggered By</th>
                  <th>When</th>
                  <th>Link</th>
                </tr>
              </thead>
              <tbody>
                {repo.recent_runs.map((run) => {
                  const c = run.conclusion || run.status || "";
                  return (
                    <tr key={run.id}>
                      <td>{run.run_number}</td>
                      <td>
                        <span className={"conclusion-badge " + c}>{c}</span>
                      </td>
                      <td>{run.head_branch}</td>
                      <td>{run.triggering_actor || "-"}</td>
                      <td style={{ color: "var(--text-muted)" }}>
                        {timeAgo(run.updated_at)}
                      </td>
                      <td>
                        <a
                          href={run.html_url ?? undefined}
                          target="_blank"
                          rel="noopener"
                          style={{
                            color: "var(--accent-blue)",
                            textDecoration: "none",
                            fontSize: 12,
                          }}
                        >
                          View
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Collapse>
      ) : null}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function TestsTab({ testRepos, ciResults }: TestsProps): React.ReactElement {
  const results = ciResults ?? [];
  return (
    <div>
      <CiSection ciResults={results} />
      <div
        style={{
          borderTop: "1px solid var(--border)",
          margin: "0 0 20px 0",
          paddingTop: 20,
        }}
      >
        <div
          style={{
            fontWeight: 700,
            fontSize: 16,
            color: "var(--text-primary)",
            marginBottom: 12,
          }}
        >
          <FlaskGlyph size={16} /> Integration Tests — Heavy Test Suite
        </div>
      </div>
      <div className="stat-row">
        <Stat
          label="Heavy Test Repos"
          value={testRepos.length}
          sub="with workflow_dispatch"
        />
        <Stat
          label="Primary"
          value="UpstreamDrift"
          sub="MuJoCo, Drake, Pinocchio"
          color="var(--accent-purple)"
        />
        <Stat label="Methods" value="2" sub="GitHub Actions + Docker" />
        <Stat label="Schedule" value="Weekly" sub="Sun 02:00 UTC" />
      </div>
      {testRepos.map((repo) => (
        <TestRepoCard key={repo.name} repo={repo} />
      ))}
    </div>
  );
}

export default TestsTab;
