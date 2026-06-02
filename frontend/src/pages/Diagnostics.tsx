/**
 * Diagnostics.tsx — the "Diagnostics" tab, extracted from the legacy
 * `App.tsx` monolith into a typed module (decomposition #836, related #834).
 *
 * Behaviour is preserved 1:1 from the legacy `DiagnosticsTab`:
 *  - loads `/api/diagnostics/summary` + `/api/deployment/git-drift` in
 *    parallel on mount and on Refresh;
 *  - renders System Overview / WSL Status / Recovery Actions / Windows
 *    Launchers / Quick API Links cards;
 *  - guards the systemd restart behind a confirm step;
 *  - generates Windows launcher scripts via `POST /api/launchers/generate`.
 *
 * LoD: the component talks only to the typed API shapes below through the
 * shared `legacyFetch` (adds the CSRF header); callers pass no props.
 * Orthogonality: a diagnostics 5xx surfaces inline and never touches Fleet.
 */
import React from "react";
import { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph } from "./decompIcons";

interface DiagnosticsSummary {
  dashboard_pid?: number | string;
  dashboard_memory_mb?: number;
  dashboard_port?: number;
  git_commit?: string;
  is_drifted?: boolean;
  source_commit?: string;
  remote_commit?: string;
  wsl_available?: boolean;
  wsl_status?: string;
}

interface GitDrift {
  is_drifted?: boolean;
  source_commit?: string;
  remote_commit?: string;
}

interface RestartResult {
  success: boolean;
  output?: string;
}

interface LauncherResult {
  message?: string;
  output_dir?: string;
  launchers?: string[];
}

export function DiagnosticsTab(): React.ReactElement {
  const [data, setData] = useState<DiagnosticsSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [driftData, setDriftData] = useState<GitDrift | null>(null);
  const [restartResult, setRestartResult] = useState<RestartResult | null>(null);
  const [restartConfirm, setRestartConfirm] = useState(false);
  const [restartLoading, setRestartLoading] = useState(false);
  const [launcherResult, setLauncherResult] = useState<LauncherResult | null>(
    null,
  );
  const [launcherLoading, setLauncherLoading] = useState(false);

  const fetchDiagnostics = useCallback(() => {
    setLoading(true);
    Promise.all([
      legacyFetch("/api/diagnostics/summary").then(
        (r) => r.json() as Promise<DiagnosticsSummary>,
      ),
      legacyFetch("/api/deployment/git-drift").then(
        (r) => r.json() as Promise<GitDrift>,
      ),
    ])
      .then((results) => {
        setData(results[0] || {});
        setDriftData(results[1] || {});
        setError(null);
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load diagnostics");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    fetchDiagnostics();
  }, [fetchDiagnostics]);

  const doRestartService = useCallback(() => {
    setRestartLoading(true);
    setRestartResult(null);
    legacyFetch("/api/diagnostics/restart-service", { method: "POST" })
      .then((r) => r.json() as Promise<RestartResult>)
      .then((d) => {
        setRestartResult(d);
        setRestartConfirm(false);
        setRestartLoading(false);
      })
      .catch((e: unknown) => {
        setRestartResult({
          success: false,
          output: e instanceof Error ? e.message : "Request failed",
        });
        setRestartConfirm(false);
        setRestartLoading(false);
      });
  }, []);

  const doGenerateLaunchers = useCallback(() => {
    setLauncherLoading(true);
    setLauncherResult(null);
    legacyFetch("/api/launchers/generate", { method: "POST" })
      .then((r) => r.json() as Promise<LauncherResult>)
      .then((d) => {
        setLauncherResult(d);
        setLauncherLoading(false);
      })
      .catch((e: unknown) => {
        setLauncherResult({
          message:
            "Error: " + (e instanceof Error ? e.message : "Request failed"),
        });
        setLauncherLoading(false);
      });
  }, []);

  const cardStyle: React.CSSProperties = {
    background: "var(--card-bg)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    padding: "16px 20px",
    marginBottom: 16,
  };
  const sectionHeadStyle: React.CSSProperties = {
    fontSize: 13,
    fontWeight: 600,
    color: "var(--text-muted)",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    marginBottom: 12,
  };
  const kvStyle: React.CSSProperties = {
    display: "flex",
    gap: 8,
    marginBottom: 6,
    fontSize: 13,
  };
  const keyStyle: React.CSSProperties = {
    color: "var(--text-muted)",
    minWidth: 140,
  };
  const valStyle: React.CSSProperties = {
    color: "var(--text-primary)",
    fontFamily: "monospace",
  };
  const btnStyle: React.CSSProperties = {
    background: "var(--accent-blue)",
    color: "var(--text-on-accent)",
    border: "none",
    borderRadius: 6,
    padding: "6px 14px",
    cursor: "pointer",
    fontSize: 13,
    marginRight: 8,
  };
  const dangerBtnStyle: React.CSSProperties = {
    ...btnStyle,
    background: "var(--accent-red)",
  };
  const warnBannerStyle: React.CSSProperties = {
    background: "rgba(210,153,34,0.15)",
    border: "1px solid var(--accent-yellow)",
    borderRadius: 6,
    padding: "10px 14px",
    marginBottom: 16,
    fontSize: 13,
    color: "var(--accent-yellow)",
  };

  if (loading) {
    return <div style={{ padding: 24 }}>Loading diagnostics…</div>;
  }
  if (error) {
    return (
      <div style={{ padding: 24, color: "var(--accent-red)" }}>
        Error: {error}
      </div>
    );
  }

  const isDrifted =
    (driftData && driftData.is_drifted) || (data && data.is_drifted);

  return (
    <div style={{ padding: 20, maxWidth: 860 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 20,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Diagnostics</h2>
        <button
          style={btnStyle}
          onClick={fetchDiagnostics}
          aria-label="Refresh diagnostics"
        >
          <RefreshGlyph size={12} /> Refresh
        </button>
      </div>

      {isDrifted ? (
        <div style={warnBannerStyle}>
          ⚠️ Deployed version is behind origin/main. Run update-deployed.sh to
          update.
          <div style={{ marginTop: 6, fontSize: 12, opacity: 0.85 }}>
            Local:{" "}
            {(driftData && driftData.source_commit) ||
              (data && data.source_commit) ||
              "?"}{" "}
            → Remote:{" "}
            {(driftData && driftData.remote_commit) ||
              (data && data.remote_commit) ||
              "?"}
          </div>
        </div>
      ) : null}

      <div style={cardStyle}>
        <div style={sectionHeadStyle}>System Overview</div>
        {data ? (
          <div>
            <div style={kvStyle}>
              <span style={keyStyle}>Dashboard PID</span>
              <span style={valStyle}>{data.dashboard_pid || "—"}</span>
            </div>
            <div style={kvStyle}>
              <span style={keyStyle}>Memory</span>
              <span style={valStyle}>
                {(data.dashboard_memory_mb || 0) + " MB"}
              </span>
            </div>
            <div style={kvStyle}>
              <span style={keyStyle}>Port</span>
              <span style={valStyle}>{data.dashboard_port || 8321}</span>
            </div>
            <div style={kvStyle}>
              <span style={keyStyle}>Git Commit</span>
              <span style={valStyle}>{data.git_commit || "unknown"}</span>
            </div>
            <div style={kvStyle}>
              <span style={keyStyle}>Drift Status</span>
              <span style={valStyle}>
                {isDrifted ? "Behind origin/main" : "Up to date"}
              </span>
            </div>
          </div>
        ) : (
          <div>No data</div>
        )}
      </div>

      <div style={cardStyle}>
        <div style={sectionHeadStyle}>WSL Status</div>
        {data ? (
          <div>
            <div
              style={{
                fontSize: 12,
                marginBottom: 8,
                color: data.wsl_available
                  ? "var(--accent-green)"
                  : "var(--accent-red)",
              }}
            >
              {data.wsl_available ? "WSL available" : "WSL not available"}
            </div>
            {data.wsl_status ? (
              <pre
                style={{
                  background: "var(--bg-secondary)",
                  borderRadius: 4,
                  padding: 10,
                  fontSize: 11,
                  margin: 0,
                  overflowX: "auto",
                  color: "var(--text-primary)",
                  whiteSpace: "pre-wrap",
                }}
              >
                {data.wsl_status}
              </pre>
            ) : null}
          </div>
        ) : (
          <div>No WSL data</div>
        )}
      </div>

      <div style={cardStyle}>
        <div style={sectionHeadStyle}>Recovery Actions</div>
        <div style={{ marginBottom: 12 }}>
          {restartConfirm ? (
            <div>
              <span
                style={{
                  fontSize: 13,
                  marginRight: 8,
                  color: "var(--accent-yellow)",
                }}
              >
                Restart the runner-dashboard systemd service?
              </span>
              <button
                style={dangerBtnStyle}
                onClick={doRestartService}
                disabled={restartLoading}
              >
                {restartLoading ? "Restarting…" : "Confirm Restart"}
              </button>
              <button
                style={{
                  ...btnStyle,
                  background: "var(--card-bg)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border)",
                }}
                onClick={() => setRestartConfirm(false)}
                aria-label="Cancel restart"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              style={dangerBtnStyle}
              onClick={() => {
                setRestartConfirm(true);
                setRestartResult(null);
              }}
            >
              Restart Dashboard Service
            </button>
          )}
          {restartResult ? (
            <div
              style={{
                marginTop: 8,
                padding: "8px 12px",
                background: restartResult.success
                  ? "rgba(63,185,80,0.1)"
                  : "rgba(248,81,73,0.1)",
                borderRadius: 4,
                fontSize: 12,
                color: restartResult.success
                  ? "var(--accent-green)"
                  : "var(--accent-red)",
              }}
            >
              {restartResult.success
                ? "Service restarted successfully."
                : "Restart failed."}
              {restartResult.output ? (
                <pre
                  style={{ margin: "4px 0 0", fontSize: 11, whiteSpace: "pre-wrap" }}
                >
                  {restartResult.output}
                </pre>
              ) : null}
            </div>
          ) : null}
        </div>
      </div>

      <div style={cardStyle}>
        <div style={sectionHeadStyle}>Windows Launchers</div>
        <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>
          Generate PowerShell scripts on your Windows Desktop for quick access.
        </p>
        <button
          style={btnStyle}
          onClick={doGenerateLaunchers}
          disabled={launcherLoading}
        >
          {launcherLoading ? "Generating…" : "Generate Launchers"}
        </button>
        {launcherResult ? (
          <div style={{ marginTop: 12, fontSize: 12 }}>
            <div style={{ color: "var(--accent-green)", marginBottom: 6 }}>
              {launcherResult.message || "Done"}
            </div>
            {launcherResult.output_dir ? (
              <div style={{ color: "var(--text-muted)", marginBottom: 6 }}>
                Output: <code>{launcherResult.output_dir}</code>
              </div>
            ) : null}
            {launcherResult.launchers && launcherResult.launchers.length > 0 ? (
              <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
                {launcherResult.launchers.map((f) => (
                  <li key={f} style={{ fontFamily: "monospace", fontSize: 11 }}>
                    {f.split(/[\\/]/).pop()}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>

      <div style={cardStyle}>
        <div style={sectionHeadStyle}>Quick API Links</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {[
            "/api/health",
            "/api/system",
            "/api/runners",
            "/api/diagnostics/summary",
            "/api/deployment/drift",
          ].map((url) => (
            <a
              key={url}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: 12,
                color: "var(--accent-blue)",
                textDecoration: "none",
                padding: "4px 8px",
                background: "var(--bg-secondary)",
                borderRadius: 4,
              }}
            >
              {url}
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
