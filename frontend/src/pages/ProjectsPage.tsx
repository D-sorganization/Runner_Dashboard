/**
 * ProjectsPage.tsx — the "Projects" tab (issue #1199, epic #1192).
 *
 * One card per fleet repository from `GET /api/projects`, ordered by the
 * owner's priority tier (P0 first) under a fleet summary bar: charter feature
 * progress (shipped / in-progress / planned / parked), the open "Decisions
 * needed" from the generated STATUS.md, and the latest project-steward run on
 * this node. "Run steward now" posts to the existing Staff Hub dispatch route
 * (`POST /api/staff/project-steward/run`) through `apiRequest`, which carries
 * the CSRF sentinel header. A repo whose charter is missing or malformed shows
 * the reason on its card; the page itself never fails on one bad repo.
 *
 * Each card also shows the repo's latest CI result from `GET /api/repos`
 * (#1338, folded in from the retired Organization tab), and its assessment
 * score history from `GET /api/assessments/scores` with a "Request assessment"
 * action (#1338, from the retired Assessments tab). The request is the existing
 * `assessment.run` Staff request, which dispatches Jules-Assess-Repo.yml; it
 * takes no staff role. Both fetches are independent: if one fails the cards
 * stay and a note names the failure.
 */
import React from "react";
import { apiRequest, ApiClientError, legacyFetch } from "../lib/api";
import { errorMessage, submitStaffRequest } from "./Staff/staffApi";
import {
  FleetSummaryBar,
  ProjectCard,
  STEWARD_RUN_BODY,
} from "./Projects";
import type { AssessmentScore, ProjectsResponse, RepoCiStatus } from "./Projects";

interface RepoCiRow {
  name?: string;
  last_ci_status?: string | null;
  last_ci_conclusion?: string | null;
  last_ci_run_url?: string | null;
}

function ciByRepo(payload: unknown): Record<string, RepoCiStatus> {
  const rows: unknown = Array.isArray(payload)
    ? payload
    : (payload as { repos?: unknown } | null)?.repos;
  const out: Record<string, RepoCiStatus> = {};
  if (!Array.isArray(rows)) return out;
  for (const row of rows as RepoCiRow[]) {
    if (!row || typeof row.name !== "string") continue;
    out[row.name] = {
      status: row.last_ci_status ?? null,
      conclusion: row.last_ci_conclusion ?? null,
      runUrl: row.last_ci_run_url ?? null,
    };
  }
  return out;
}

function scoresByRepo(payload: unknown): Record<string, AssessmentScore[]> {
  const rows: unknown = Array.isArray(payload)
    ? payload
    : (payload as { scores?: unknown } | null)?.scores;
  const out: Record<string, AssessmentScore[]> = {};
  if (!Array.isArray(rows)) return out;
  for (const row of rows as AssessmentScore[]) {
    if (!row || typeof row.repo !== "string") continue;
    (out[row.repo] ??= []).push(row);
  }
  return out;
}

function describeError(err: unknown): string {
  if (err instanceof ApiClientError) return `${err.status}: ${err.message}`;
  return err instanceof Error ? err.message : String(err);
}

export function ProjectsPage(): React.ReactElement {
  const [data, setData] = React.useState<ProjectsResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [running, setRunning] = React.useState<Record<string, boolean>>({});
  const [notices, setNotices] = React.useState<Record<string, string>>({});
  const [ci, setCi] = React.useState<Record<string, RepoCiStatus>>({});
  const [ciError, setCiError] = React.useState<string | null>(null);
  const [scores, setScores] = React.useState<Record<string, AssessmentScore[]>>({});
  const [scoresError, setScoresError] = React.useState<string | null>(null);
  const [assessing, setAssessing] = React.useState<Record<string, boolean>>({});
  const [assessNotices, setAssessNotices] = React.useState<Record<string, string>>({});

  const load = React.useCallback(() => {
    setLoading(true);
    apiRequest<ProjectsResponse>("/api/projects")
      .then((payload) => {
        setData(payload);
        setLoadError(null);
      })
      .catch((err: unknown) => setLoadError(describeError(err)))
      .finally(() => setLoading(false));
    legacyFetch("/api/repos")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((payload: unknown) => {
        setCi(ciByRepo(payload));
        setCiError(null);
      })
      .catch((err: unknown) => {
        setCi({});
        setCiError(describeError(err));
      });
    legacyFetch("/api/assessments/scores")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((payload: unknown) => {
        setScores(scoresByRepo(payload));
        setScoresError(null);
      })
      .catch((err: unknown) => {
        setScores({});
        setScoresError(describeError(err));
      });
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const runSteward = React.useCallback((repo: string) => {
    setRunning((prev) => ({ ...prev, [repo]: true }));
    submitStaffRequest({
      kind: "staff.dispatch",
      role: "project-steward",
      target: { repo, ref: "" },
      prompt: STEWARD_RUN_BODY.prompt,
      machine: STEWARD_RUN_BODY.machine,
      dry_run: false,
    })
      .then((resp) => {
        const runId = resp.run_id || resp.result?.run_id;
        const status = resp.result?.status ?? "queued";
        const id = runId
          ? ` (run ${runId.slice(0, 8)}, ${status})`
          : "";
        setNotices((prev) => ({
          ...prev,
          [repo]: `Steward run submitted${id}.`,
        }));
      })
      .catch((err: unknown) => {
        setNotices((prev) => ({
          ...prev,
          [repo]: `Steward run failed — ${errorMessage(err)}`,
        }));
      })
      .finally(() => setRunning((prev) => ({ ...prev, [repo]: false })));
  }, []);

  const requestAssessment = React.useCallback((repo: string, provider: string) => {
    setAssessing((prev) => ({ ...prev, [repo]: true }));
    submitStaffRequest({
      kind: "assessment.run",
      target: { repo, ref: "" },
      provider: provider || null,
      prompt: "",
      machine: "local",
      dry_run: false,
    })
      .then((resp) => {
        const runId = resp.run_id || resp.result?.run_id;
        const id = runId ? ` (run ${runId.slice(0, 8)})` : "";
        setAssessNotices((prev) => ({
          ...prev,
          [repo]: `Assessment requested${id}.`,
        }));
      })
      .catch((err: unknown) => {
        setAssessNotices((prev) => ({
          ...prev,
          [repo]: `Assessment request failed — ${errorMessage(err)}`,
        }));
      })
      .finally(() => setAssessing((prev) => ({ ...prev, [repo]: false })));
  }, []);

  return (
    <div className="projects-page">
      <div
        className="section-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h2 style={{ margin: 0 }}>Projects</h2>
        <button
          type="button"
          className="btn"
          onClick={load}
          disabled={loading}
          aria-label="Refresh projects"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>
      {loadError && (
        <p role="alert" style={{ color: "var(--accent-red)" }}>
          Could not load projects — {loadError}
        </p>
      )}
      {ciError && (
        <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          CI status unavailable — {ciError}
        </p>
      )}
      {scoresError && (
        <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
          Assessment scores unavailable — {scoresError}
        </p>
      )}
      {!loadError && data?.summary && (
        <FleetSummaryBar
          summary={data.summary}
          prioritiesError={data.priorities_error}
        />
      )}
      {!loadError && data && data.projects.length === 0 && (
        <p style={{ color: "var(--text-secondary)" }}>
          No repositories configured in config/projects.json.
        </p>
      )}
      <div
        className="projects-grid"
        style={{
          display: "grid",
          gap: 12,
          gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
        }}
      >
        {(data?.projects ?? []).map((project) => (
          <ProjectCard
            key={project.repo}
            project={project}
            running={Boolean(running[project.repo])}
            notice={notices[project.repo]}
            ci={ci[project.repo]}
            onRunSteward={runSteward}
            assessments={scores[project.repo]}
            assessing={Boolean(assessing[project.repo])}
            assessmentNotice={assessNotices[project.repo]}
            onRequestAssessment={requestAssessment}
          />
        ))}
      </div>
    </div>
  );
}

export default ProjectsPage;
