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
 */
import React from "react";
import { apiRequest, ApiClientError } from "../lib/api";
import {
  FleetSummaryBar,
  ProjectCard,
  STEWARD_RUN_BODY,
  STEWARD_RUN_URL,
} from "./Projects";
import type { ProjectsResponse } from "./Projects";

interface DispatchResponse {
  run?: { id?: string; status?: string };
  dry_run?: boolean;
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

  const load = React.useCallback(() => {
    setLoading(true);
    apiRequest<ProjectsResponse>("/api/projects")
      .then((payload) => {
        setData(payload);
        setLoadError(null);
      })
      .catch((err: unknown) => setLoadError(describeError(err)))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  const runSteward = React.useCallback((repo: string) => {
    setRunning((prev) => ({ ...prev, [repo]: true }));
    apiRequest<DispatchResponse>(STEWARD_RUN_URL, {
      body: { repo, ...STEWARD_RUN_BODY },
    })
      .then((resp) => {
        const id = resp.run?.id
          ? ` (run ${resp.run.id.slice(0, 8)}, ${resp.run.status ?? "queued"})`
          : "";
        setNotices((prev) => ({
          ...prev,
          [repo]: `Steward run submitted${id}.`,
        }));
      })
      .catch((err: unknown) => {
        setNotices((prev) => ({
          ...prev,
          [repo]: `Steward run failed — ${describeError(err)}`,
        }));
      })
      .finally(() => setRunning((prev) => ({ ...prev, [repo]: false })));
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
            onRunSteward={runSteward}
          />
        ))}
      </div>
    </div>
  );
}

export default ProjectsPage;
