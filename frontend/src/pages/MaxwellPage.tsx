/**
 * MaxwellPage.tsx — the "Maxwell" tab, extracted (behaviour-wise 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 6).
 *
 * Maxwell-Daemon is a staff provider, and this page is its provider-status view
 * (#1338, SC-G6 owner decision): a status stat row (service status, HTTP
 * reachability, binary discovery, contract version), start/stop/restart
 * controls, and a recent-tasks table. Chat with Maxwell lives in the Staff
 * Console (#1330), which the page links to. The daemon is reached over HTTP; the
 * dashboard never imports from the Maxwell-Daemon repo (see CLAUDE.md
 * cross-repo rule).
 *
 * Presentational shell: the daemon `status` (and its poll) is owned by the
 * legacy App, so this page receives the already-fetched `status`, a `loading`
 * flag, an `error` string, and `onRefresh` / `onControl` callbacks. Tasks,
 * version and control-status are local state fetched directly from the
 * `/api/maxwell/*` endpoints.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph, ServerGlyph } from "./decompIcons";
import { MaxwellTasksPanel } from "./MaxwellPanels";

// ── Types ──────────────────────────────────────────────────────────────────

export interface MaxwellStatus {
  status?: string;
  service_detail?: string;
  service_running?: boolean;
  http_reachable?: boolean;
  http_detail?: string;
  binary_found?: boolean;
  binary_path?: string;
  dashboard_url?: string;
}

export interface MaxwellTask {
  id?: string;
  status?: string;
  repo?: string;
  created_at?: string;
}

export interface MaxwellControlPayload {
  action: string;
}

export interface MaxwellProps {
  status?: MaxwellStatus;
  loading?: boolean;
  error?: string;
  onRefresh?: () => void;
  onControl: (payload: MaxwellControlPayload) => Promise<unknown>;
}

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

export function MaxwellPage(): React.ReactElement {
  const [status, setStatus] = useState<MaxwellStatus>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>();

  const fetchStatus = useCallback(() => {
    setLoading(true);
    legacyFetch("/api/maxwell/status")
      .then((r) => r.json())
      .then((data: MaxwellStatus) => {
        setStatus(data || {});
        setError(undefined);
      })
      .catch(() => {
        setError("Failed to probe Maxwell status.");
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const controlDaemon = useCallback((payload: MaxwellControlPayload) => {
    return legacyFetch("/api/maxwell/control", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    }).then((r) =>
      r.json().then((data: unknown) => {
        if (!r.ok) {
          const detail =
            data &&
            typeof data === "object" &&
            "detail" in data &&
            typeof data.detail === "string"
              ? data.detail
              : "Control failed";
          throw new Error(detail);
        }
        return data;
      }),
    );
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  return (
    <MaxwellTab
      status={status}
      loading={loading}
      error={error}
      onRefresh={fetchStatus}
      onControl={controlDaemon}
    />
  );
}

export function MaxwellTab({
  status,
  loading,
  error,
  onRefresh,
  onControl,
}: MaxwellProps): React.ReactElement {
  const st = status || {};
  const [controlStatus, setControlStatus] = useState<{
    ok: boolean;
    msg: string;
  } | null>(null);
  const [controlling, setControlling] = useState(false);
  const [pendingAction, setPendingAction] = useState("");
  const [tasks, setTasks] = useState<MaxwellTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [daemonVersion, setDaemonVersion] = useState("");
  const isRunning = st.status === "running";

  function fetchTasks(): void {
    setTasksLoading(true);
    legacyFetch("/api/maxwell/tasks?limit=10")
      .then((r) => r.json())
      .then((data: { tasks?: MaxwellTask[] }) => {
        setTasks(data.tasks || []);
      })
      .catch(() => {
        setTasks([]);
      })
      .finally(() => {
        setTasksLoading(false);
      });
  }

  function fetchVersion(): void {
    legacyFetch("/api/maxwell/version")
      .then((r) => r.json())
      .then((data: { contract?: string; daemon?: string }) => {
        setDaemonVersion(data.contract || data.daemon || "");
      })
      .catch(() => {
        setDaemonVersion("");
      });
  }

  useEffect(() => {
    fetchTasks();
    fetchVersion();
  }, []);

  function doControl(action: string): void {
    setPendingAction(action);
    setControlling(true);
    setControlStatus(null);
    onControl({ action })
      .then(() => {
        setControlStatus({ ok: true, msg: "Requested " + action + "." });
        if (onRefresh) setTimeout(onRefresh, 1000);
      })
      .catch((err: Error) => {
        setControlStatus({
          ok: false,
          msg: "Could not " + action + " Maxwell: " + err.message,
        });
      })
      .finally(() => {
        setControlling(false);
        setPendingAction("");
      });
  }

  return (
    <div>
      <div className="stat-row">
        <Stat
          label="Status"
          value={st.status || "unknown"}
          sub={st.service_detail || ""}
        />
        <Stat
          label="HTTP"
          value={st.http_reachable ? "reachable" : "offline"}
          sub={st.http_reachable ? st.http_detail || "" : "not listening"}
        />
        <Stat
          label="Binary"
          value={st.binary_found ? "found" : "missing"}
          sub={st.binary_path || "not on PATH"}
        />
        <Stat label="Contract" value={daemonVersion || "unknown"} />
      </div>
      <div
        style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}
      >
        <button
          className="btn"
          onClick={onRefresh}
          disabled={loading}
          aria-label="Refresh Maxwell status"
        >
          <RefreshGlyph size={12} />
          {loading ? "Refreshing..." : "Refresh"}
        </button>
        {!isRunning ? (
          <button
            className="btn"
            onClick={() => {
              doControl("start");
            }}
            disabled={controlling}
            aria-label="Start Maxwell daemon"
          >
            Start Maxwell
          </button>
        ) : null}
        {isRunning ? (
          <button
            className="btn"
            onClick={() => {
              doControl("stop");
            }}
            disabled={controlling}
            aria-label="Stop Maxwell daemon"
          >
            Stop Maxwell
          </button>
        ) : null}
        {isRunning ? (
          <button
            className="btn"
            onClick={() => {
              doControl("restart");
            }}
            disabled={controlling}
            aria-label="Restart Maxwell daemon"
          >
            Restart Maxwell
          </button>
        ) : null}
      </div>
      {controlStatus ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            marginBottom: 12,
            background: controlStatus.ok
              ? "rgba(63,185,80,0.12)"
              : "rgba(248,81,73,0.12)",
            color: controlStatus.ok
              ? "var(--accent-green)"
              : "var(--accent-red)",
          }}
        >
          {controlStatus.msg}
        </div>
      ) : null}
      {error ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            marginBottom: 12,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
          }}
        >
          {error}
        </div>
      ) : null}
      <div className="section">
        <div className="section-header">
          <span className="section-title">
            <ServerGlyph size={14} />
            Maxwell-Daemon
          </span>
        </div>
        <div className="section-body">
          {pendingAction ? (
            <div
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                marginBottom: 8,
              }}
            >
              {"Working on " + pendingAction + "..."}
            </div>
          ) : null}
          {st.dashboard_url ? (
            <a
              href={st.dashboard_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--accent-blue)" }}
            >
              {st.dashboard_url + " ↗"}
            </a>
          ) : null}
          {!st.binary_found && !st.service_running && !st.http_reachable ? (
            <div
              style={{
                marginTop: 12,
                fontSize: 12,
                color: "var(--text-muted)",
              }}
            >
              Maxwell-Daemon is not detected on this machine.
            </div>
          ) : null}
        </div>
      </div>
      <p style={{ fontSize: 13, color: "var(--text-muted)" }}>
        <a href="/" style={{ color: "var(--accent-blue)" }}>
          Chat with Maxwell in the Staff Console
        </a>
      </p>
      <MaxwellTasksPanel
        status={st}
        tasks={tasks}
        tasksLoading={tasksLoading}
      />
    </div>
  );
}

export default MaxwellTab;
