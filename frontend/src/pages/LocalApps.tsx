/**
 * LocalApps.tsx — the "Local Tools" tab, extracted (behaviour-wise 1:1) from
 * the legacy `App.tsx` monolith as part of the decomposition epic (#836, pass
 * 3).
 *
 * Monitors locally-deployed tools/apps: git drift (behind/ahead), dirty working
 * trees, systemd service status, and HTTP health probes, with headline badges
 * for how many need attention.
 *
 * Presentational: the local-apps payload (and its poll) is owned by the legacy
 * App because the same data also feeds the sidebar attention badge. To stay DRY
 * and avoid double-polling, this page receives the already-fetched `data`, a
 * `loading` flag, and an `onRefresh` callback. The render is wrapped in a local
 * error boundary (ported from the legacy `LocalAppsErrorBoundary`) so a
 * malformed app entry degrades to a Retry affordance instead of taking down the
 * shell (orthogonality). Loading/empty states and a11y semantics match the
 * original legacy render exactly.
 */
import React from "react";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph } from "./decompIcons";
import {
  localAppHasUpdateAvailable,
  localAppUnhealthy,
} from "./localAppStatus";

/** Git drift summary for a local app. */
interface LocalAppDrift {
  available?: boolean;
  behind?: number;
  ahead?: number;
  ref?: string | null;
  error?: string | null;
}

/** HTTP health-probe summary for a local app. */
interface LocalAppHealth {
  available?: boolean;
  status?: string | null;
  ok?: boolean;
  status_code?: number | null;
}

/** A single monitored local application/tool. */
export interface LocalApp {
  name: string;
  drift?: LocalAppDrift;
  health?: LocalAppHealth;
  service_status?: string | null;
  deployed_version?: string | null;
  deployment?: { version?: string | null };
  dirty?: boolean;
  dirty_files?: string[];
  dirty_available?: boolean;
  dirty_error?: string | null;
}

/** The local-apps payload owned by the legacy App and passed down here. */
export interface LocalAppsData {
  tools?: LocalApp[];
  apps?: LocalApp[];
  manifest_path?: string | null;
}

export interface LocalAppsProps {
  /** The local-apps payload (tools/apps + manifest path). */
  data: LocalAppsData;
  /** True while the payload is being (re)fetched. */
  loading: boolean;
  /** Trigger an immediate re-fetch. */
  onRefresh: () => void;
}

// Pure status predicates live in ./localAppStatus.

export function LocalAppsPage(): React.ReactElement {
  const [data, setData] = React.useState<LocalAppsData>({ tools: [] });
  const [loading, setLoading] = React.useState(true);

  const fetchLocalApps = React.useCallback(() => {
    setLoading(true);
    legacyFetch("/api/local-apps")
      .then((r) => r.json())
      .then((payload: LocalAppsData | null) => {
        if (payload) setData(payload);
      })
      .catch(() => {
        /* keep last-known data */
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  React.useEffect(() => {
    fetchLocalApps();
  }, [fetchLocalApps]);

  return (
    <LocalAppsTab data={data} loading={loading} onRefresh={fetchLocalApps} />
  );
}

function renderVersion(value?: string | null): string {
  return value && value !== "unknown" ? value : "unknown";
}

const badgeBase = "section-badge";

function DriftBadge({ app }: { app: LocalApp }): React.ReactElement {
  const d = app.drift || {};
  const behind = d.behind || 0;
  const ahead = d.ahead || 0;
  if (!d.available) {
    return (
      <span
        className={badgeBase}
        style={{
          background: "rgba(248,81,73,0.15)",
          color: "var(--accent-red)",
        }}
        title={d.error || "unavailable"}
      >
        {"⚠ error"}
      </span>
    );
  }
  if (behind === 0 && ahead === 0) {
    return (
      <span
        className={badgeBase}
        style={{
          background: "rgba(63,185,80,0.15)",
          color: "var(--accent-green)",
        }}
      >
        {"✔ current"}
      </span>
    );
  }
  if (behind > 0 && ahead === 0) {
    return (
      <span
        className={badgeBase}
        style={{
          background: "rgba(210,153,34,0.15)",
          color: "var(--accent-yellow)",
        }}
      >
        {"▼ " + behind + " behind"}
      </span>
    );
  }
  if (ahead > 0 && behind === 0) {
    return (
      <span
        className={badgeBase}
        style={{
          background: "rgba(88,166,255,0.15)",
          color: "var(--accent-blue)",
        }}
      >
        {"▲ " + ahead + " ahead"}
      </span>
    );
  }
  return (
    <span
      className={badgeBase}
      style={{ background: "rgba(248,81,73,0.15)", color: "var(--accent-red)" }}
    >
      {"↔ diverged"}
    </span>
  );
}

function HealthBadge({ app }: { app: LocalApp }): React.ReactElement {
  const h2 = app.health || {};
  if (!h2.available || h2.status === "not-configured") {
    return (
      <span
        className={badgeBase}
        style={{
          background: "rgba(110,118,129,0.2)",
          color: "var(--text-muted)",
        }}
      >
        {"—"}
      </span>
    );
  }
  const ok = h2.ok !== false;
  return (
    <span
      className={badgeBase}
      title={h2.status_code ? "HTTP " + h2.status_code : ""}
      style={{
        background: ok ? "rgba(63,185,80,0.15)" : "rgba(248,81,73,0.15)",
        color: ok ? "var(--accent-green)" : "var(--accent-red)",
      }}
    >
      {ok ? "✔ " + (h2.status || "ok") : "✗ " + (h2.status || "fail")}
    </span>
  );
}

function ServiceBadge({
  status,
}: {
  status?: string | null;
}): React.ReactElement | null {
  if (!status || status === "not-configured") return null;
  const ok = status === "active";
  return (
    <span
      className={badgeBase}
      style={{
        background: ok ? "rgba(63,185,80,0.15)" : "rgba(248,81,73,0.15)",
        color: ok ? "var(--accent-green)" : "var(--accent-red)",
      }}
    >
      {status}
    </span>
  );
}

function DirtyBadge({ app }: { app: LocalApp }): React.ReactElement {
  if (app.dirty_available === false) {
    return (
      <span
        className={badgeBase}
        title={app.dirty_error || "dirty probe failed"}
        style={{
          background: "rgba(248,81,73,0.15)",
          color: "var(--accent-red)",
        }}
      >
        {"⚠ probe error"}
      </span>
    );
  }
  if (app.dirty) {
    return (
      <span
        style={{ color: "var(--accent-yellow)", fontWeight: 600 }}
        title={(app.dirty_files || []).join("\n")}
      >
        {app.dirty_files
          ? app.dirty_files.length +
            " file" +
            (app.dirty_files.length !== 1 ? "s" : "")
          : "yes"}
      </span>
    );
  }
  return <span style={{ color: "var(--text-muted)" }}>clean</span>;
}

// ── Page body ────────────────────────────────────────────────────────────────

function LocalAppsBody({
  data,
  loading,
  onRefresh,
}: LocalAppsProps): React.ReactElement {
  const apps: LocalApp[] = Array.isArray(data.tools)
    ? data.tools
    : Array.isArray(data.apps)
      ? data.apps
      : [];

  const behindCount = apps.filter(localAppHasUpdateAvailable).length;
  const unhealthyCount = apps.filter(localAppUnhealthy).length;
  const dirtyCount = apps.filter((a) => a.dirty).length;
  const dirtyErrorCount = apps.filter(
    (a) => a.dirty_available === false,
  ).length;

  return (
    <div className="section">
      <div
        className="section-header"
        style={{ display: "flex", alignItems: "center", gap: 8 }}
      >
        <span>Local Tools</span>
        {behindCount > 0 ? (
          <span
            className={badgeBase}
            style={{
              background: "rgba(210,153,34,0.15)",
              color: "var(--accent-yellow)",
            }}
          >
            {behindCount +
              " update" +
              (behindCount > 1 ? "s" : "") +
              " available"}
          </span>
        ) : null}
        {unhealthyCount > 0 ? (
          <span
            className={badgeBase}
            style={{
              background: "rgba(248,81,73,0.15)",
              color: "var(--accent-red)",
            }}
          >
            {unhealthyCount + " unhealthy"}
          </span>
        ) : null}
        {dirtyCount > 0 ? (
          <span
            className={badgeBase}
            style={{
              background: "rgba(210,153,34,0.15)",
              color: "var(--accent-yellow)",
            }}
          >
            {dirtyCount + " dirty"}
          </span>
        ) : null}
        {dirtyErrorCount > 0 ? (
          <span
            className={badgeBase}
            style={{
              background: "rgba(248,81,73,0.15)",
              color: "var(--accent-red)",
            }}
          >
            {dirtyErrorCount + " dirty probe error"}
          </span>
        ) : null}
        <button
          className="btn"
          type="button"
          style={{ marginLeft: "auto" }}
          onClick={onRefresh}
          aria-label="Refresh local tools"
        >
          <RefreshGlyph size={12} />
        </button>
      </div>

      {loading ? (
        <div style={{ padding: 24, color: "var(--text-muted)" }}>
          {"Loading…"}
        </div>
      ) : apps.length === 0 ? (
        <div style={{ padding: 24, color: "var(--text-muted)" }}>
          {data.manifest_path
            ? "No tools defined in local_apps.json."
            : "No local_apps.json manifest found. Add one to runner-dashboard/ to start monitoring."}
        </div>
      ) : (
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <th>App</th>
              <th>Drift Ref</th>
              <th>Version</th>
              <th>Drift</th>
              <th>Dirty</th>
              <th>Service</th>
              <th>Health</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((app) => (
              <React.Fragment key={app.name}>
                <tr>
                  <td>
                    <strong>{app.name}</strong>
                    {app.dirty ? (
                      <span
                        className={badgeBase}
                        style={{
                          marginLeft: 4,
                          background: "rgba(210,153,34,0.15)",
                          color: "var(--accent-yellow)",
                        }}
                        title={
                          "Uncommitted local changes:\n" +
                          (app.dirty_files || []).join("\n")
                        }
                      >
                        {"⚠ dirty"}
                      </span>
                    ) : null}
                  </td>
                  <td
                    style={{
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: "var(--text-muted)",
                    }}
                  >
                    {(app.drift && app.drift.ref) || "—"}
                  </td>
                  <td
                    style={{
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: "var(--text-muted)",
                    }}
                  >
                    {renderVersion(
                      app.deployed_version ||
                        (app.deployment && app.deployment.version),
                    )}
                  </td>
                  <td>
                    <DriftBadge app={app} />
                  </td>
                  <td>
                    <DirtyBadge app={app} />
                  </td>
                  <td>
                    <ServiceBadge status={app.service_status} />
                  </td>
                  <td>
                    <HealthBadge app={app} />
                  </td>
                </tr>
                {app.dirty && app.dirty_files && app.dirty_files.length > 0 ? (
                  <tr>
                    <td
                      colSpan={7}
                      style={{
                        color: "var(--text-muted)",
                        fontSize: 11,
                        fontFamily: "monospace",
                        padding: "2px 12px 6px",
                      }}
                    >
                      {app.dirty_files.slice(0, 5).join(", ") +
                        (app.dirty_files.length > 5
                          ? " +" + (app.dirty_files.length - 5) + " more"
                          : "")}
                    </td>
                  </tr>
                ) : null}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Error boundary (ported from legacy LocalAppsErrorBoundary) ───────────────

interface BoundaryState {
  hasError: boolean;
  error: Error | null;
}

/**
 * Wraps the page body so a malformed app entry degrades to a Retry affordance
 * instead of crashing the shell (orthogonality — issue #836).
 */
export class LocalAppsTab extends React.Component<
  LocalAppsProps,
  BoundaryState
> {
  constructor(props: LocalAppsProps) {
    super(props);
    this.state = { hasError: false, error: null };
    this.handleRetry = this.handleRetry.bind(this);
  }

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("[LocalAppsTab] render error:", error, info);
  }

  handleRetry(): void {
    this.setState({ hasError: false, error: null });
  }

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 24, color: "var(--text-primary)" }}>
          <div
            style={{
              marginBottom: 12,
              color: "var(--accent-red)",
              fontWeight: 600,
            }}
          >
            Local Tools failed to render
          </div>
          <code
            style={{
              display: "block",
              fontSize: 12,
              color: "var(--accent-red)",
              marginBottom: 12,
              whiteSpace: "pre-wrap",
            }}
          >
            {String(this.state.error)}
          </code>
          <button
            className="btn"
            type="button"
            onClick={this.handleRetry}
            aria-label="Retry loading data"
          >
            Retry
          </button>
        </div>
      );
    }
    return <LocalAppsBody {...this.props} />;
  }
}

export default LocalAppsTab;
