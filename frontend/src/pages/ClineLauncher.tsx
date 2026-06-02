/**
 * ClineLauncher.tsx — the "Cline Launcher" tab, extracted from the legacy
 * `App.tsx` monolith into a typed module (decomposition #836, related #834).
 *
 * Behaviour is preserved 1:1 from the legacy `ClineLauncherTab`:
 *  - polls `/api/agent-launcher/status` every 5s + on demand;
 *  - discovers repos via `/api/agent-launcher/repos` (503 ⇒ "not installed");
 *  - start/stop the scheduler and "run once" per agent through
 *    `POST /api/agent-launcher/<verb>`;
 *  - surfaces per-action busy state and a single error banner.
 *
 * The launcher's own state lives on the user's local filesystem — the
 * dashboard only mirrors it, so this tab manages its own polling rather than
 * bloating the parent (Orthogonality: a launcher 5xx never touches Fleet).
 *
 * LoD: the component talks only to the typed API shapes below via the shared
 * `legacyFetch` (adds the CSRF header); callers pass no props.
 */
import { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";

interface LauncherAgent {
  name: string;
  enabled: boolean;
  interval_seconds: number;
  last_run_iso?: string | null;
  last_repo?: string | null;
  last_window_pid?: number | null;
  lock_alive?: boolean;
}

interface LauncherStatus {
  scheduler_running?: boolean;
  scheduler_pid?: number | null;
  runtime_root?: string | null;
  agents?: LauncherAgent[];
}

interface LauncherRepo {
  name: string;
  wsl_path?: string;
}

interface LauncherRepos {
  count: number;
  org_filter?: string;
  wsl_distro?: string;
  repos_root?: string;
  repos?: LauncherRepo[];
}

interface RunOnceBody {
  agent: string;
}

/** Per-action busy flags keyed by verb (and optional "/<agent>"). */
type BusyMap = Record<string, boolean>;

export function ClineLauncherTab(): React.ReactElement {
  const [status, setStatus] = useState<LauncherStatus | null>(null);
  const [repos, setRepos] = useState<LauncherRepos | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<BusyMap>({});

  const fetchStatus = useCallback(() => {
    legacyFetch("/api/agent-launcher/status")
      .then((resp) => {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json() as Promise<LauncherStatus>;
      })
      .then((j) => {
        setStatus(j);
        setError("");
      })
      .catch((e: unknown) => setError(String(e)));
  }, []);

  const fetchRepos = useCallback(() => {
    setLoading(true);
    legacyFetch("/api/agent-launcher/repos")
      .then((resp) => {
        if (resp.status === 503) {
          throw new Error("launcher not installed on this machine");
        }
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json() as Promise<LauncherRepos>;
      })
      .then((j) => {
        setRepos(j);
        setError("");
      })
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  const action = useCallback(
    (verb: string, body?: RunOnceBody) => {
      const key = verb + (body && body.agent ? "/" + body.agent : "");
      setBusy((prev) => ({ ...prev, [key]: true }));
      const opts: RequestInit = { method: "POST" };
      if (body) {
        opts.headers = { "Content-Type": "application/json" };
        opts.body = JSON.stringify(body);
      }
      return legacyFetch("/api/agent-launcher/" + verb, opts)
        .then((resp) =>
          resp.json().then((j: { detail?: string }) => {
            if (!resp.ok) throw new Error(j.detail || "HTTP " + resp.status);
            return j;
          }),
        )
        .then(() => fetchStatus())
        .catch((e: unknown) => setError(String(e)))
        .finally(() =>
          setBusy((prev) => {
            const next = { ...prev };
            delete next[key];
            return next;
          }),
        );
    },
    [fetchStatus],
  );

  useEffect(() => {
    fetchStatus();
    fetchRepos();
    const t = setInterval(fetchStatus, 5000);
    return () => clearInterval(t);
  }, [fetchStatus, fetchRepos]);

  const schedRunning = Boolean(status && status.scheduler_running);
  const statusBadge = (
    <span
      className="section-badge"
      style={{
        background: schedRunning
          ? "rgba(63,185,80,0.15)"
          : "rgba(248,81,73,0.15)",
        color: schedRunning ? "var(--accent-green)" : "var(--accent-red)",
      }}
    >
      {schedRunning ? "running" : "stopped"}
    </span>
  );

  return (
    <div className="tab-panel">
      {/* Header + global controls */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}
      >
        <h2 style={{ margin: 0 }}>Cline Agent Launcher</h2>
        {statusBadge}
        {status && status.scheduler_pid ? (
          <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
            {"pid " + status.scheduler_pid}
          </span>
        ) : null}
      </div>

      {error ? (
        <div className="alert alert-error" style={{ marginBottom: 12 }}>
          {error}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <button
          className="btn btn-primary"
          disabled={Boolean(busy["start"]) || schedRunning}
          onClick={() => action("start")}
          aria-label="Start scheduler"
        >
          {busy["start"] ? "starting..." : "Start scheduler"}
        </button>
        <button
          className="btn"
          disabled={Boolean(busy["stop"]) || !schedRunning}
          onClick={() => action("stop")}
          aria-label="Stop scheduler"
        >
          {busy["stop"] ? "stopping..." : "Stop scheduler"}
        </button>
        <button
          className="btn"
          disabled={loading}
          onClick={() => {
            fetchRepos();
            fetchStatus();
          }}
          aria-label="Refresh scheduler status"
        >
          {loading ? "refreshing..." : "Refresh"}
        </button>
      </div>

      {/* Agents table */}
      <h3>Agents</h3>
      {status && status.agents && status.agents.length ? (
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>Agent</th>
              <th>Enabled</th>
              <th>Interval</th>
              <th>Last run (UTC)</th>
              <th>Last repo</th>
              <th>Window PID</th>
              <th>Lock</th>
              <th>Run now</th>
            </tr>
          </thead>
          <tbody>
            {status.agents.map((a) => (
              <tr key={a.name}>
                <td>{a.name}</td>
                <td>{a.enabled ? "yes" : "no"}</td>
                <td>{a.interval_seconds + "s"}</td>
                <td style={{ fontSize: 12 }}>{a.last_run_iso || "-"}</td>
                <td>{a.last_repo || "-"}</td>
                <td>{a.last_window_pid || "-"}</td>
                <td>
                  {a.lock_alive ? (
                    <span style={{ color: "var(--accent-yellow)" }}>alive</span>
                  ) : (
                    "-"
                  )}
                </td>
                <td>
                  <button
                    className="btn btn-sm"
                    disabled={Boolean(busy["run-once/" + a.name]) || a.lock_alive}
                    onClick={() => action("run-once", { agent: a.name })}
                  >
                    {busy["run-once/" + a.name] ? "spawning..." : "Run once"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div style={{ color: "var(--text-secondary)" }}>
          {
            "No agents configured. Edit %LOCALAPPDATA%\\cline_agent_launcher\\config.json"
          }
        </div>
      )}

      {/* Discovered repo inventory */}
      <h3 style={{ marginTop: 24 }}>
        Discovered repositories
        {repos ? (
          <span
            className="section-badge"
            style={{
              marginLeft: 8,
              background: "rgba(136,108,228,0.15)",
              color: "var(--accent-purple)",
            }}
          >
            {repos.count + " " + (repos.org_filter || "")}
          </span>
        ) : null}
      </h3>
      {repos && repos.repos && repos.repos.length ? (
        <div
          style={{
            fontSize: 12,
            color: "var(--text-secondary)",
            marginBottom: 8,
          }}
        >
          {"WSL distro: " + repos.wsl_distro + ", root: " + repos.repos_root}
        </div>
      ) : null}
      {repos && repos.repos ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {repos.repos.map((r) => (
            <span
              key={r.name}
              className="section-badge"
              title={r.wsl_path}
              style={{ background: "var(--bg-tertiary)" }}
            >
              {r.name}
            </span>
          ))}
        </div>
      ) : (
        <div style={{ color: "var(--text-secondary)" }}>
          {loading ? "discovering..." : "No repos discovered yet."}
        </div>
      )}

      {/* Notes */}
      <div
        style={{
          marginTop: 24,
          padding: 12,
          background: "var(--bg-tertiary)",
          borderRadius: 4,
          fontSize: 12,
          color: "var(--text-secondary)",
        }}
      >
        <div>{"Runtime root: " + ((status && status.runtime_root) || "-")}</div>
        <div>
          Status polls every 5s. Repo discovery is on-demand (Refresh button).
        </div>
        <div>
          Full config editor (model selector, repo multi-select, intervals)
          ships in a follow-up — for now edit config.json directly and click
          Refresh.
        </div>
      </div>
    </div>
  );
}
