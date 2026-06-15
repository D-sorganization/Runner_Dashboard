import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { OverviewEventSection } from "./Events";
import { FleetTab } from "./FleetTab";
import { OverviewLeases } from "./OverviewLeases";
import { ActivityGlyph } from "./decompIcons";
import { legacyFetch } from "../lib/api";
import {
  alertContentHash,
  computeFleetAlerts,
  type FleetAlert,
} from "../lib/fleetAlerts";
import { tabIdToPath } from "../shell/routing";

interface OverviewState {
  runners: any[];
  runs: any[];
  system: Record<string, any>;
  stats: Record<string, any>;
  queue: Record<string, any>;
  machinesData: Record<string, any>;
  watchdog: Record<string, any>;
  deployment: Record<string, any>;
  runnerAudit: Record<string, any>;
}

const EMPTY_STATE: OverviewState = {
  runners: [],
  runs: [],
  system: {},
  stats: {},
  queue: {},
  machinesData: { nodes: [] },
  watchdog: {},
  deployment: {},
  runnerAudit: { violations: [] },
};

function normalizeArrayPayload(payload: unknown, key: string): any[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    const value = (payload as Record<string, unknown>)[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function normalizeObjectPayload(payload: unknown): Record<string, any> {
  return payload && typeof payload === "object"
    ? (payload as Record<string, any>)
    : {};
}

function normalizeNodesPayload(payload: unknown): Record<string, any> {
  const objectPayload = normalizeObjectPayload(payload);
  return Array.isArray(objectPayload.nodes) ? objectPayload : { nodes: [] };
}

function telemetryAlert(
  id: "telemetry-degraded" | "github-api",
  level: "warning" | "critical",
  title: string,
  detail: string,
): FleetAlert {
  return {
    id,
    level,
    title,
    detail,
    contentHash: alertContentHash({ id, level, title, detail }),
  };
}

function buildOverviewAlerts(
  state: OverviewState,
  githubStatus: Record<string, any>,
  telemetryError: string | null,
): FleetAlert[] {
  const nodes = Array.isArray(state.machinesData.nodes)
    ? state.machinesData.nodes
    : [];
  const base = computeFleetAlerts({
    machineCount: nodes.length,
    machineOnline: nodes.filter((n: any) => n && n.online).length,
    machineNodes: nodes,
    watchdog: state.watchdog,
    stats: state.stats,
    completedRuns: state.stats.runs_completed || 0,
    runnerAudit: state.runnerAudit,
  }).alerts;
  const alerts = base.slice();
  if (telemetryError) {
    alerts.push(
      telemetryAlert(
        "telemetry-degraded",
        "warning",
        "Fleet telemetry degraded",
        telemetryError,
      ),
    );
  }
  if (
    githubStatus.status === "rate_limited" ||
    githubStatus.status === "auth_error"
  ) {
    const isAuthError = githubStatus.status === "auth_error";
    alerts.push(
      telemetryAlert(
        "github-api",
        isAuthError ? "critical" : "warning",
        "GitHub API degraded",
        isAuthError
          ? "Authentication failed. Refresh the dashboard GitHub token before relying on GitHub-backed views."
          : "Rate limited. Cached local runner data may still be shown.",
      ),
    );
  }
  return alerts;
}

function getJson(url: string, signal?: AbortSignal): Promise<unknown> {
  return legacyFetch(url, { signal }).then((response) => {
    if (!response.ok) {
      throw new Error(url + " HTTP " + response.status);
    }
    return response.json();
  });
}

export function OverviewPage(): React.ReactElement {
  const navigate = useNavigate();
  const [state, setState] = useState<OverviewState>(EMPTY_STATE);
  const [githubStatus, setGithubStatus] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    Promise.all([
      getJson("/api/stats", signal),
      getJson("/api/runners", signal),
      getJson("/api/runs?per_page=30", signal),
      getJson("/api/system", signal),
      getJson("/api/queue", signal),
      getJson("/api/fleet/nodes", signal),
      getJson("/api/watchdog", signal),
      getJson("/api/deployment", signal),
      getJson("/api/runner-routing-audit", signal),
      getJson("/api/github/status", signal),
    ])
      .then(
        ([
          stats,
          runners,
          runs,
          system,
          queue,
          machinesData,
          watchdog,
          deployment,
          runnerAudit,
          github,
        ]) => {
          setState({
            stats: normalizeObjectPayload(stats),
            runners: normalizeArrayPayload(runners, "runners"),
            runs: normalizeArrayPayload(runs, "runs"),
            system: normalizeObjectPayload(system),
            queue: normalizeObjectPayload(queue),
            machinesData: normalizeNodesPayload(machinesData),
            watchdog: normalizeObjectPayload(watchdog),
            deployment: normalizeObjectPayload(deployment),
            runnerAudit: normalizeObjectPayload(runnerAudit),
          });
          setGithubStatus(normalizeObjectPayload(github));
        },
      )
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load overview data.",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const onFleet = useCallback(
    (action: string) => {
      setActionLoading(true);
      legacyFetch("/api/fleet/control/" + action, { method: "POST" })
        .then((response) => {
          if (!response.ok) throw new Error("fleet control HTTP " + response.status);
        })
        .then(() => refresh())
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Fleet action failed.");
        })
        .finally(() => setActionLoading(false));
    },
    [refresh],
  );

  const onRunner = useCallback(
    (id: string | number, action: string) => {
      setActionLoading(true);
      legacyFetch("/api/runners/" + id + "/" + action, { method: "POST" })
        .then((response) => {
          if (!response.ok) throw new Error("runner action HTTP " + response.status);
        })
        .then(() => refresh())
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Runner action failed.");
        })
        .finally(() => setActionLoading(false));
    },
    [refresh],
  );

  const appAlerts = useMemo(
    () => buildOverviewAlerts(state, githubStatus, error),
    [state, githubStatus, error],
  );

  const onNavigate = useCallback(
    (tabId: string) => {
      navigate(tabIdToPath(tabId));
    },
    [navigate],
  );

  const onAlertNavigate = useCallback(
    (alertId: FleetAlert["id"]) => {
      if (alertId === "hosted-runners" || alertId === "github-api") {
        onNavigate("runner-audit");
        return;
      }
      if (alertId === "machines-offline" || alertId === "telemetry-degraded") {
        onNavigate("machines");
        return;
      }
      if (alertId === "disk-pressure" || alertId === "runners-offline") {
        onNavigate("events");
        return;
      }
      onNavigate("overview");
    },
    [onNavigate],
  );

  return (
    <div>
      {error ? (
        <div
          className="section"
          role="alert"
          style={{ marginBottom: 12, color: "var(--accent-red)" }}
        >
          Failed to load overview data: {error}
          <button
            className="btn"
            type="button"
            onClick={() => refresh()}
            style={{ marginLeft: 12 }}
          >
            Retry
          </button>
        </div>
      ) : null}
      <FleetTab
        runners={state.runners}
        runs={state.runs}
        system={state.system}
        stats={state.stats}
        queue={state.queue}
        machinesData={state.machinesData}
        onFleet={onFleet}
        onRunner={onRunner}
        loading={loading || actionLoading}
        watchdog={state.watchdog}
        deployment={state.deployment}
        setTab={onNavigate}
        runnerAudit={state.runnerAudit}
        onOpenDeployment={() => onNavigate("deployment")}
      />
      <div className="section section--stacked">
        <div className="section-header">
          <div className="section-title">
            <ActivityGlyph size={16} />
            Alarms & Recent Events
          </div>
          <button
            className="btn section-header__action"
            type="button"
            onClick={() => onNavigate("events")}
          >
            Open Event Log
          </button>
        </div>
        <div className="section-body">
          <OverviewEventSection
            rollupAlerts={appAlerts}
            onNavigate={onAlertNavigate}
          />
        </div>
      </div>
      <OverviewLeases />
    </div>
  );
}

export default OverviewPage;
