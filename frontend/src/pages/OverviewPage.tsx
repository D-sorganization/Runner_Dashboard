/* eslint-disable @typescript-eslint/no-explicit-any -- OverviewPage adapts heterogeneous fleet payloads into typed sections. */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FleetStatusBanner,
  FleetMachinesSection,
  FleetRunnersSection,
  FleetAlertsSection,
  FleetEventsSection,
} from "./Fleet";
import { OverviewLeases } from "./OverviewLeases";
import { legacyFetch } from "../lib/api";
import {
  alertContentHash,
  computeFleetAlerts,
  type FleetAlert,
} from "../lib/fleetAlerts";
import { useFleetEvents } from "../hooks/useFleetEvents";
import { tabIdToPath } from "../shell/routing";

interface OverviewState {
  runners: any[];
  runs: any[];
  system: Record<string, any>;
  stats: Record<string, any>;
  queue: Record<string, any>;
  machinesData: { nodes: any[] };
  watchdog: Record<string, any>;
  deployment: Record<string, any>;
  runnerAudit: { violations?: any[]; last_checked?: string | null; error?: string | null };
  driftInfo: { is_drifted?: boolean; summary?: string } | null;
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
  driftInfo: null,
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

function normalizeNodesPayload(payload: unknown): { nodes: any[] } {
  const objectPayload = normalizeObjectPayload(payload);
  return Array.isArray(objectPayload.nodes) ? { nodes: objectPayload.nodes } : { nodes: [] };
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
  failedSources: string[] = [],
  isStale: boolean = false,
  runnersLoaded: boolean = false,
  nodesLoaded: boolean = false,
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
    runnersLoaded,
    nodesLoaded,
    failedSources,
    isStale,
    error: telemetryError,
  }).alerts;
  const alerts = base.slice();
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
  const [runnersLoaded, setRunnersLoaded] = useState(false);
  const [nodesLoaded, setNodesLoaded] = useState(false);
  const [failedSources, setFailedSources] = useState<string[]>([]);
  const [lastSuccessAt, setLastSuccessAt] = useState<number | null>(null);
  const [isStale, setIsStale] = useState(false);

  // Independent error states per section (SC-G2 failure mode isolation)
  const [machinesError, setMachinesError] = useState<string | null>(null);
  const [runnersError, setRunnersError] = useState<string | null>(null);
  const [auditError, setAuditError] = useState<string | null>(null);

  const {
    events,
    loading: eventsLoading,
    error: eventsError,
    refetch: eventsRefetch,
  } = useFleetEvents();

  useEffect(() => {
    const timer = setInterval(() => {
      if (lastSuccessAt && Date.now() - lastSuccessAt >= 60_000) {
        setIsStale(true);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [lastSuccessAt]);

  // Support smooth hash scrolling for deep links (#machines, #runners, #alerts, #events)
  useEffect(() => {
    const hash = window.location.hash;
    if (hash) {
      const element = document.querySelector(hash);
      if (element) {
        element.scrollIntoView({ behavior: "smooth" });
      }
    }
  }, []);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    setMachinesError(null);
    setRunnersError(null);
    setAuditError(null);

    const endpoints: Array<{ key: keyof OverviewState | "github"; url: string }> = [
      { key: "stats", url: "/api/stats" },
      { key: "runners", url: "/api/runners" },
      { key: "runs", url: "/api/runs?per_page=30" },
      { key: "system", url: "/api/system" },
      { key: "queue", url: "/api/queue" },
      { key: "machinesData", url: "/api/fleet/nodes" },
      { key: "watchdog", url: "/api/watchdog" },
      { key: "deployment", url: "/api/deployment" },
      { key: "runnerAudit", url: "/api/runner-routing-audit" },
      { key: "github", url: "/api/github/status" },
      { key: "driftInfo", url: "/api/deployment/git-drift" },
    ];

    Promise.allSettled(
      endpoints.map((ep) =>
        getJson(ep.url, signal).then((data) => ({ key: ep.key, url: ep.url, data })),
      ),
    )
      .then((results) => {
        if (signal?.aborted) return;
        const failed: string[] = [];
        let rLoaded = false;
        let nLoaded = false;
        const updates: Partial<OverviewState> = {};
        let githubPayload: Record<string, any> = {};

        results.forEach((res, idx) => {
          const ep = endpoints[idx];
          if (res.status === "fulfilled") {
            const data = res.value.data;
            if (ep.key === "runners") {
              updates.runners = normalizeArrayPayload(data, "runners");
              rLoaded = true;
            } else if (ep.key === "machinesData") {
              updates.machinesData = normalizeNodesPayload(data);
              nLoaded = true;
            } else if (ep.key === "runs") {
              updates.runs = normalizeArrayPayload(data, "runs");
            } else if (ep.key === "stats") {
              updates.stats = normalizeObjectPayload(data);
            } else if (ep.key === "system") {
              updates.system = normalizeObjectPayload(data);
            } else if (ep.key === "queue") {
              updates.queue = normalizeObjectPayload(data);
            } else if (ep.key === "watchdog") {
              updates.watchdog = normalizeObjectPayload(data);
            } else if (ep.key === "deployment") {
              updates.deployment = normalizeObjectPayload(data);
            } else if (ep.key === "runnerAudit") {
              updates.runnerAudit = normalizeObjectPayload(data);
            } else if (ep.key === "github") {
              githubPayload = normalizeObjectPayload(data);
            } else if (ep.key === "driftInfo") {
              updates.driftInfo = normalizeObjectPayload(data);
            }
          } else {
            const err = res.reason;
            if (err instanceof DOMException && err.name === "AbortError") return;
            const errMsg = err instanceof Error ? err.message : String(err);
            const formatted = errMsg.includes(ep.url) ? errMsg : `${ep.url}: ${errMsg}`;
            failed.push(formatted);

            // Per-section error classification
            if (ep.key === "machinesData") setMachinesError(formatted);
            if (ep.key === "runners") setRunnersError(formatted);
            if (ep.key === "runnerAudit") setAuditError(formatted);
          }
        });

        setState((prev) => ({
          ...prev,
          ...updates,
        }));
        setGithubStatus(githubPayload);
        setRunnersLoaded((prev) => prev || rLoaded);
        setNodesLoaded((prev) => prev || nLoaded);
        setFailedSources(failed);
        if (failed.length > 0) {
          setError(failed.join("; "));
        } else {
          setError(null);
        }
        if (rLoaded && nLoaded) {
          setLastSuccessAt(Date.now());
          setIsStale(false);
        }
      })
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

  const onRefreshAudit = useCallback(() => {
    legacyFetch("/api/runner-routing-audit/refresh", { method: "POST" })
      .then(() => {
        setTimeout(() => {
          getJson("/api/runner-routing-audit")
            .then((data) => {
              setState((prev) => ({
                ...prev,
                runnerAudit: normalizeObjectPayload(data),
              }));
              setAuditError(null);
            })
            .catch((e: unknown) => {
              setAuditError(e instanceof Error ? e.message : "Failed to refresh audit");
            });
        }, 1500);
      })
      .catch((e: unknown) => {
        setAuditError(e instanceof Error ? e.message : "Failed to trigger audit refresh");
      });
  }, []);

  const onAskMaintenance = useCallback(
    (target: string, prompt: string) => {
      // SC-E6: Route fleet maintenance questions to Staff Console Maintenance role
      navigate(`/?role=maintenance&prompt=${encodeURIComponent(`[${target}] ${prompt}`)}`);
    },
    [navigate],
  );

  const appAlerts = useMemo(
    () =>
      buildOverviewAlerts(
        state,
        githubStatus,
        error,
        failedSources,
        isStale,
        runnersLoaded,
        nodesLoaded,
      ),
    [state, githubStatus, error, failedSources, isStale, runnersLoaded, nodesLoaded],
  );

  return (
    <div className="overview-page" style={{ padding: "0.5rem" }}>
      {/* 1. Status Banner: tri-state health honesty (SC-A2) and jump anchors */}
      <FleetStatusBanner
        loading={loading}
        runnersLoaded={runnersLoaded}
        nodesLoaded={nodesLoaded}
        failedSources={failedSources}
        isStale={isStale}
        error={error}
        runners={state.runners}
        stats={state.stats}
        driftInfo={state.driftInfo}
        onRetry={() => refresh()}
        onOpenDeployment={() => navigate(tabIdToPath("operations") + "#deploy")}
      />

      {/* 2. Machines Section: single unified machines table with expandable telemetry */}
      <FleetMachinesSection
        nodes={state.machinesData?.nodes || []}
        runners={state.runners}
        loading={loading}
        error={machinesError}
        onRetry={() => refresh()}
        onAskMaintenance={onAskMaintenance}
      />

      {/* 3. Runners Section: filter pills, fleet controls, runner list with maintenance */}
      <FleetRunnersSection
        runners={state.runners}
        runs={state.runs}
        loading={loading || actionLoading}
        error={runnersError}
        onRetry={() => refresh()}
        onFleet={onFleet}
        onRunner={onRunner}
        onAskMaintenance={onAskMaintenance}
      />

      {/* 4. Alerts & Hosted-Runner Billing Section: active alarms + routing audit */}
      <FleetAlertsSection
        alerts={appAlerts}
        runnerAudit={state.runnerAudit}
        loading={loading}
        error={auditError}
        onRefreshAudit={onRefreshAudit}
        onRetry={() => refresh()}
      />

      {/* 5. Event Log Section: durable recent fleet events with severity filters */}
      <div data-testid="overview-events">
        <FleetEventsSection
          events={events}
          loading={eventsLoading}
          error={eventsError}
          onRetry={eventsRefetch}
        />
      </div>

      {/* 6. Active Leases Strip */}
      <OverviewLeases />
    </div>
  );
}

export default OverviewPage;
