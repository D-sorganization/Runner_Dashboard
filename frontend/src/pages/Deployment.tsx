/**
 * Deployment.tsx — the "Deployment" tab, extracted (behaviour-wise 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 5).
 *
 * Shows fleet-wide rollout state: a stat row (rollout status, machines needing
 * attention, online count, expected vs current version), a per-machine list with
 * desired/deployed versions + drift, and a dry-run "Preview update" → "Confirm
 * update" flow that POSTs to `/api/deployment/update-signal`.
 *
 * Presentational: the deployment state (and its poll) is owned by the legacy App,
 * so this page receives the already-fetched `data`, a `loading` flag, and
 * `onRefresh` / `onOpenFleet` callbacks. The dry-run preview is local state.
 * Loading/empty states and a11y semantics mirror the original legacy render.
 */
import React, { useState } from "react";
import { Stat } from "../components/Stat";
import { timeAgo } from "../components/formatters";
import { legacyFetch } from "../lib/api";

// ── Types ──────────────────────────────────────────────────────────────────

interface RolloutState {
  status?: string;
  summary?: string;
  machines_attention?: number;
  machines_online?: number;
  machines_total?: number;
}

interface DriftStatus {
  severity?: string;
  update_available?: boolean;
  message?: string;
  current?: string;
  expected?: string;
}

export interface DeploymentMachine {
  name: string;
  display_name?: string;
  rollout_state?: string;
  rollout_label?: string;
  rollout_detail?: string;
  desired_version?: string;
  deployed_version?: string;
  drift_status?: DriftStatus;
  last_health_check?: string | null;
  last_rollback?: string | null;
}

export interface DeploymentData {
  rollout_state?: RolloutState;
  machines?: DeploymentMachine[];
  expected_version?: string;
  drift?: DriftStatus;
}

export interface DeploymentProps {
  data?: DeploymentData;
  loading?: boolean;
  onRefresh?: () => void;
  onOpenFleet?: () => void;
}

interface PreviewState {
  loading?: boolean;
  machine: DeploymentMachine;
  title?: string;
  preview?: unknown;
  drift?: unknown;
  result?: unknown;
  error?: string;
  confirmed?: boolean;
  confirming?: boolean;
  dryRun?: boolean;
}

// Rollout-state ordering for the per-machine list (most-urgent first), 1:1 legacy.
const ROLLOUT_PRIORITY: Record<string, number> = {
  dirty: 0,
  offline: 1,
  drifted: 2,
  degraded: 3,
  unknown: 4,
  steady: 5,
};

function renderVersion(value?: string): string {
  return value && value !== "unknown" ? value : "unknown";
}

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

export function DeploymentTab({
  data,
  loading,
  onRefresh,
  onOpenFleet,
}: DeploymentProps): React.ReactElement {
  const d = data || {};
  const refresh = onRefresh || (() => {});
  const openFleet = onOpenFleet || (() => {});
  const [preview, setPreview] = useState<PreviewState | null>(null);

  const rollout = d.rollout_state || {};
  const machines = (d.machines || []).slice().sort((a, b) => {
    const ap =
      ROLLOUT_PRIORITY[a.rollout_state || ""] != null
        ? ROLLOUT_PRIORITY[a.rollout_state || ""]
        : 9;
    const bp =
      ROLLOUT_PRIORITY[b.rollout_state || ""] != null
        ? ROLLOUT_PRIORITY[b.rollout_state || ""]
        : 9;
    if (ap !== bp) return ap - bp;
    return (a.display_name || a.name || "").localeCompare(
      b.display_name || b.name || "",
    );
  });

  function refreshAfterAction(): void {
    refresh();
    setTimeout(refresh, 1500);
  }

  function previewUpdate(machine: DeploymentMachine): void {
    if (!machine || !machine.name) return;
    setPreview({
      loading: true,
      machine,
      title: "Loading dry-run preview...",
    });
    legacyFetch("/api/deployment/update-signal", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        node: machine.name,
        reason: "dashboard-ui",
        dry_run: true,
      }),
    })
      .then((r) => r.json())
      .then((resp) => {
        setPreview({
          loading: false,
          machine,
          preview: resp.preview || null,
          drift: resp.drift || null,
          dryRun: true,
        });
      })
      .catch(() => {
        setPreview({
          loading: false,
          machine,
          error: "Dry-run preview failed.",
        });
      });
  }

  function confirmUpdate(): void {
    if (!preview || !preview.machine) return;
    const machine = preview.machine;
    setPreview({ ...preview, loading: true, confirming: true });
    legacyFetch("/api/deployment/update-signal", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        node: machine.name,
        reason: "dashboard-ui",
      }),
    })
      .then((r) => r.json())
      .then((resp) => {
        setPreview({
          loading: false,
          machine,
          result: resp.event || null,
          drift: resp.drift || null,
          confirmed: true,
        });
        refreshAfterAction();
      })
      .catch(() => {
        setPreview({
          loading: false,
          machine,
          error: "Update signal failed.",
        });
      });
  }

  const attentionCount = rollout.machines_attention || 0;
  const onlineCount = rollout.machines_online || 0;
  const totalCount = rollout.machines_total || machines.length;

  return (
    <div>
      <div className="stat-row">
        <Stat
          label="Rollout"
          value={rollout.status || "unknown"}
          color={
            rollout.status === "stable"
              ? "var(--accent-green)"
              : rollout.status === "blocked"
                ? "var(--accent-red)"
                : rollout.status === "degraded"
                  ? "var(--accent-yellow)"
                  : "inherit"
          }
          sub={rollout.summary || "Deployment state across the fleet"}
        />
        <Stat
          label="Attention"
          value={attentionCount}
          color={attentionCount > 0 ? "var(--accent-yellow)" : "inherit"}
          sub="offline, drifting, dirty, or unknown machines"
        />
        <Stat
          label="Online"
          value={onlineCount + "/" + totalCount}
          color={
            totalCount > 0 && onlineCount === totalCount
              ? "var(--accent-green)"
              : "var(--accent-yellow)"
          }
          sub="machines reporting dashboard telemetry"
        />
        <Stat
          label="Expected"
          value={renderVersion(d.expected_version)}
          sub="hub VERSION target"
        />
        <Stat
          label="Current"
          value={renderVersion((d.drift || {}).current)}
          sub={(d.drift || {}).message || "current deployment"}
        />
      </div>
      <div className="deployment-note">
        <span>Deployment state for</span>
        <code>
          {renderVersion((d.drift || {}).expected || d.expected_version)}
        </code>
        <button
          className="btn"
          style={{ padding: "0 8px", fontSize: 11, height: 22 }}
          onClick={openFleet}
        >
          Fleet overview
        </button>
        {loading ? <span>Loading...</span> : null}
      </div>
      {preview ? (
        <div className="deployment-preview">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              flexWrap: "wrap",
            }}
          >
            <strong>
              {preview.loading
                ? "Building dry-run preview"
                : preview.confirmed
                  ? "Update signal sent"
                  : preview.error
                    ? "Preview error"
                    : "Dry-run preview"}
            </strong>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {preview.preview && !preview.confirmed && !preview.error ? (
                <button className="btn" onClick={confirmUpdate}>
                  Confirm update
                </button>
              ) : null}
              <button className="btn" onClick={() => setPreview(null)}>
                Clear
              </button>
            </div>
          </div>
          <pre>
            {preview.error
              ? preview.error
              : JSON.stringify(
                  preview.preview || preview.drift || preview.result || {},
                  null,
                  2,
                )}
          </pre>
        </div>
      ) : null}
      <div className="deployment-state-machine-list">
        {machines.map((machine) => {
          const drift = machine.drift_status || {};
          const statusTone =
            machine.rollout_state === "steady"
              ? "var(--accent-green)"
              : machine.rollout_state === "dirty" ||
                  machine.rollout_state === "offline"
                ? "var(--accent-red)"
                : machine.rollout_state === "drifted" ||
                    machine.rollout_state === "degraded"
                  ? "var(--accent-yellow)"
                  : "inherit";
          return (
            <div
              key={machine.name}
              className="deployment-state-machine"
            >
              <div className="deployment-state-machine-head">
                <div className="deployment-state-machine-title">
                  <strong>{machine.display_name || machine.name}</strong>
                  <span
                    className="section-badge"
                    style={{
                      alignSelf: "flex-start",
                      background: "rgba(88,166,255,0.12)",
                      color: statusTone,
                    }}
                  >
                    {machine.rollout_label ||
                      machine.rollout_state ||
                      "unknown"}
                  </span>
                </div>
                <button
                  className="btn"
                  style={{ padding: "0 8px", fontSize: 11, height: 22 }}
                  onClick={() => previewUpdate(machine)}
                  disabled={machine.rollout_state === "steady"}
                >
                  Preview update
                </button>
              </div>
              <div className="deployment-state-fields">
                <div className="deployment-state-field">
                  <span>Desired</span>
                  <code>{renderVersion(machine.desired_version)}</code>
                </div>
                <div className="deployment-state-field">
                  <span>Deployed</span>
                  <code>{renderVersion(machine.deployed_version)}</code>
                </div>
                <div className="deployment-state-field">
                  <span>Drift</span>
                  <span>
                    {drift.severity || "unknown"}
                    {drift.update_available ? " update available" : ""}
                  </span>
                </div>
                <div className="deployment-state-field">
                  <span>Last health check</span>
                  <span>
                    {machine.last_health_check
                      ? timeAgo(machine.last_health_check)
                      : "not recorded"}
                  </span>
                </div>
                <div className="deployment-state-field">
                  <span>Last rollback</span>
                  <span>{machine.last_rollback || "not recorded"}</span>
                </div>
              </div>
              <div
                style={{
                  fontSize: 12,
                  color: "var(--text-muted)",
                  lineHeight: 1.45,
                }}
              >
                {machine.rollout_detail ||
                  drift.message ||
                  "Deployment metadata unavailable."}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default DeploymentTab;
