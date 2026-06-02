/**
 * FleetOrchestration.tsx — the "Fleet Orchestration" tab, extracted
 * (behaviour-wise 1:1) from the legacy `App.tsx` monolith as part of the
 * decomposition epic (#836, pass 5).
 *
 * Shows a sortable table of every fleet machine (role / status / runner counts /
 * cpu+mem / last ping), a "Deploy Action" form (target machine + action +
 * confirm checkbox → `onDeploy`), an orchestration audit log (last 10), and a
 * "Dispatch Workflow" modal (repo / workflow / ref / machine target →
 * `onDispatch`).
 *
 * Presentational: fleet-orchestration data (and its poll) is owned by the legacy
 * App, so this page receives the already-fetched `data`, a `loading` flag, an
 * optional `error`, and `onRefresh` / `onDispatch` / `onDeploy` callbacks. All
 * form/modal/sort state is local. Loading/empty states and a11y semantics mirror
 * the original legacy render exactly.
 *
 * Note on `approved_by` / `requested_by`: the legacy code read a `principal`
 * binding that was out of scope in the `FleetOrchestrationTab` sibling function,
 * so it always resolved to the `"anonymous"` fallback. That behaviour is
 * preserved here verbatim.
 */
import React, { useState } from "react";
import { Stat } from "../components/Stat";
import { PlayGlyph, RefreshGlyph } from "./decompIcons";
import { SortTh } from "./decompSortTh";
import { sortRows, type SortState, type SortAccessors } from "./decompSort";

// ── Types ──────────────────────────────────────────────────────────────────

export interface OrchestrationMachine {
  name: string;
  display_name?: string;
  role?: string;
  online?: boolean;
  runner_count?: number | null;
  busy_runners?: number | null;
  cpu_percent?: number | null;
  memory_percent?: number | null;
  last_ping?: string | null;
  dashboard_url?: string | null;
}

export interface OrchestrationAuditEntry {
  audit_id?: string | number;
  event_id?: string | number;
  recorded_at?: string | null;
  orchestration_type?: string;
  action?: string;
  machine_target?: string;
  machine?: string;
  target?: string;
  deploy_action?: string;
  workflow?: string;
  requested_by?: string;
  decision?: string;
}

export interface FleetOrchestrationData {
  machines?: OrchestrationMachine[];
  audit_log?: OrchestrationAuditEntry[];
  online_count?: number;
  total_count?: number;
}

export interface OrchestrationDispatchPayload {
  repo: string;
  workflow: string;
  ref: string;
  machine_target: string;
  approved_by: string;
}

export interface OrchestrationDeployPayload {
  machine: string;
  action: string;
  confirmed: boolean;
  requested_by: string;
}

export interface FleetOrchestrationProps {
  data?: FleetOrchestrationData;
  loading?: boolean;
  error?: string | null;
  onRefresh: () => void;
  onDispatch: (payload: OrchestrationDispatchPayload) => Promise<{ audit_id?: string }>;
  onDeploy: (payload: OrchestrationDeployPayload) => Promise<{ message?: string }>;
}

// Legacy `principal` was out of scope here → always "anonymous" (see header).
const ACTOR = "anonymous";

function machineStatusColor(online?: boolean): string {
  return online ? "var(--accent-green)" : "var(--accent-red)";
}
function machineStatusBg(online?: boolean): string {
  return online ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)";
}

const MACHINE_ACCESSORS: SortAccessors<OrchestrationMachine> = {
  machine: (m) => m.display_name || m.name,
  role: (m) => m.role || "node",
  status: (m) => (m.online ? 1 : 0),
  runners: (m) => m.runner_count || 0,
  busy: (m) => m.busy_runners || 0,
  cpu: (m) => m.cpu_percent || 0,
  memory: (m) => m.memory_percent || 0,
  lastPing: (m) => m.last_ping || "",
};

export function FleetOrchestrationTab({
  data,
  loading,
  error,
  onRefresh,
  onDispatch,
  onDeploy,
}: FleetOrchestrationProps): React.ReactElement {
  const d = data || {};
  const machines = d.machines || [];
  const auditLog = d.audit_log || [];

  // Dispatch modal state
  const [dispatchModalOpen, setDispatchModalOpen] = useState(false);
  const [dispatchRepo, setDispatchRepo] = useState("");
  const [dispatchWorkflow, setDispatchWorkflow] = useState("");
  const [dispatchBranch, setDispatchBranch] = useState("main");
  const [dispatchMachineTarget, setDispatchMachineTarget] = useState("");
  const [dispatchLoading, setDispatchLoading] = useState(false);
  const [dispatchError, setDispatchError] = useState<string | null>(null);
  const [dispatchSuccess, setDispatchSuccess] = useState<string | null>(null);

  // Deploy section state
  const [deployMachine, setDeployMachine] = useState("");
  const [deployAction, setDeployAction] = useState("restart_runner");
  const [deployConfirm, setDeployConfirm] = useState(false);
  const [deployLoading, setDeployLoading] = useState(false);
  const [deployError, setDeployError] = useState<string | null>(null);
  const [deploySuccess, setDeploySuccess] = useState<string | null>(null);

  const [machineSort, setMachineSort] = useState<SortState>({
    key: "machine",
    dir: "asc",
  });
  const sortedMachines = sortRows(machines, machineSort, MACHINE_ACCESSORS);

  // `dispatchSuccess` mirrors the legacy state but, as in the original, it is
  // only ever surfaced via the closed modal flow; referenced here to keep the
  // setter meaningful without altering behaviour.
  void dispatchSuccess;

  function handleDispatch(): void {
    if (!dispatchRepo || !dispatchWorkflow) {
      setDispatchError("Repo and workflow are required.");
      return;
    }
    setDispatchLoading(true);
    setDispatchError(null);
    setDispatchSuccess(null);
    onDispatch({
      repo: dispatchRepo,
      workflow: dispatchWorkflow,
      ref: dispatchBranch || "main",
      machine_target: dispatchMachineTarget,
      approved_by: ACTOR,
    })
      .then((resp) => {
        setDispatchSuccess("Dispatched! audit_id=" + (resp.audit_id || ""));
        setDispatchLoading(false);
        setDispatchModalOpen(false);
        onRefresh();
      })
      .catch((e: { message?: string }) => {
        setDispatchError((e && e.message) || "Dispatch failed.");
        setDispatchLoading(false);
      });
  }

  function handleDeploy(): void {
    if (!deployMachine) {
      setDeployError("Select a machine.");
      return;
    }
    if (!deployConfirm) {
      setDeployError("Check the confirmation box before deploying.");
      return;
    }
    setDeployLoading(true);
    setDeployError(null);
    setDeploySuccess(null);
    onDeploy({
      machine: deployMachine,
      action: deployAction,
      confirmed: true,
      requested_by: ACTOR,
    })
      .then((resp) => {
        setDeploySuccess(resp.message || "Deployed successfully.");
        setDeployLoading(false);
        setDeployConfirm(false);
        onRefresh();
      })
      .catch((e: { message?: string }) => {
        setDeployError((e && e.message) || "Deploy failed.");
        setDeployLoading(false);
      });
  }

  const onlineCount = d.online_count || 0;
  const totalCount = d.total_count || machines.length;

  return (
    <div>
      <div className="stat-row">
        <Stat label="Online" value={onlineCount} sub="machines reachable" />
        <Stat label="Total" value={totalCount} sub="fleet machines" />
        <Stat
          label="Offline"
          value={totalCount - onlineCount}
          sub="unreachable"
        />
      </div>
      <div
        style={{
          display: "flex",
          gap: 8,
          marginBottom: 16,
          alignItems: "center",
          flexWrap: "wrap",
        }}
      >
        <button className="btn" onClick={onRefresh} disabled={loading}>
          <RefreshGlyph size={12} />
          {loading ? "Loading…" : "Refresh"}
        </button>
        <button
          className="btn"
          onClick={() => {
            setDispatchModalOpen(true);
            setDispatchError(null);
            setDispatchSuccess(null);
          }}
        >
          <PlayGlyph size={12} />
          Dispatch Workflow
        </button>
      </div>
      {error ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
            fontSize: 12,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      ) : null}
      <div
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          marginBottom: 20,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            fontWeight: 600,
            fontSize: 13,
          }}
        >
          Fleet Machines
        </div>
        <table className="data-table" style={{ width: "100%" }}>
          <thead>
            <tr>
              <SortTh label="Machine" sortKey="machine" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="Role" sortKey="role" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="Status" sortKey="status" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="Runners" sortKey="runners" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="Busy" sortKey="busy" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="CPU %" sortKey="cpu" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="Mem %" sortKey="memory" sort={machineSort} setSort={setMachineSort} />
              <SortTh label="Last Ping" sortKey="lastPing" sort={machineSort} setSort={setMachineSort} />
            </tr>
          </thead>
          <tbody>
            {machines.length === 0 ? (
              <tr>
                <td
                  colSpan={8}
                  style={{
                    textAlign: "center",
                    color: "var(--text-muted)",
                    padding: 20,
                  }}
                >
                  {loading ? "Loading machines…" : "No machines found."}
                </td>
              </tr>
            ) : (
              sortedMachines.map((m) => (
                <tr key={m.name}>
                  <td>
                    <span style={{ fontWeight: 600 }}>
                      {m.display_name || m.name}
                    </span>
                    {m.dashboard_url ? (
                      <a
                        href={m.dashboard_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          marginLeft: 6,
                          fontSize: 11,
                          color: "var(--text-muted)",
                        }}
                      >
                        ↗
                      </a>
                    ) : null}
                  </td>
                  <td
                    style={{ color: "var(--text-secondary)", fontSize: 12 }}
                  >
                    {m.role || "node"}
                  </td>
                  <td>
                    <span
                      className="section-badge"
                      style={{
                        background: machineStatusBg(m.online),
                        color: machineStatusColor(m.online),
                      }}
                    >
                      {m.online ? "Online" : "Offline"}
                    </span>
                  </td>
                  <td>{m.runner_count != null ? m.runner_count : "—"}</td>
                  <td>{m.busy_runners != null ? m.busy_runners : "—"}</td>
                  <td>
                    {m.cpu_percent != null
                      ? m.cpu_percent.toFixed(0) + "%"
                      : "—"}
                  </td>
                  <td>
                    {m.memory_percent != null
                      ? m.memory_percent.toFixed(0) + "%"
                      : "—"}
                  </td>
                  <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {m.last_ping
                      ? new Date(m.last_ping).toLocaleTimeString()
                      : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          padding: 16,
          marginBottom: 20,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 12 }}>
          Deploy Action
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <div>
            <label
              style={{
                display: "block",
                fontSize: 12,
                marginBottom: 4,
                color: "var(--text-secondary)",
              }}
            >
              Target Machine
            </label>
            <select
              className="input"
              style={{ width: "100%" }}
              value={deployMachine}
              onChange={(e) => setDeployMachine(e.target.value)}
            >
              <option value="">— select machine —</option>
              {machines.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.display_name || m.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label
              style={{
                display: "block",
                fontSize: 12,
                marginBottom: 4,
                color: "var(--text-secondary)",
              }}
            >
              Action
            </label>
            <select
              className="input"
              style={{ width: "100%" }}
              value={deployAction}
              onChange={(e) => setDeployAction(e.target.value)}
            >
              <option value="restart_runner">Restart Runner</option>
              <option value="sync_workflows">Sync Workflows</option>
              <option value="update_config">Update Config</option>
            </select>
          </div>
        </div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 12,
            marginBottom: 12,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={deployConfirm}
            onChange={(e) => setDeployConfirm(e.target.checked)}
          />
          I confirm this action against the selected machine
        </label>
        {deployError ? (
          <div
            style={{
              color: "var(--accent-red)",
              fontSize: 12,
              marginBottom: 8,
            }}
          >
            {deployError}
          </div>
        ) : null}
        {deploySuccess ? (
          <div
            style={{
              color: "var(--accent-green)",
              fontSize: 12,
              marginBottom: 8,
            }}
          >
            {deploySuccess}
          </div>
        ) : null}
        <button
          className="btn"
          onClick={handleDeploy}
          disabled={deployLoading || !deployConfirm}
        >
          {deployLoading ? "Deploying…" : "Deploy"}
        </button>
      </div>
      <div
        style={{
          background: "var(--bg-secondary)",
          border: "1px solid var(--border)",
          borderRadius: 10,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid var(--border)",
            fontWeight: 600,
            fontSize: 13,
          }}
        >
          Orchestration Audit Log (last 10)
        </div>
        {auditLog.length === 0 ? (
          <div
            style={{
              padding: 20,
              textAlign: "center",
              color: "var(--text-muted)",
              fontSize: 12,
            }}
          >
            No audit entries yet.
          </div>
        ) : (
          <table className="data-table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Target</th>
                <th>Action</th>
                <th>By</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {auditLog.map((entry, idx) => (
                <tr key={entry.audit_id || entry.event_id || idx}>
                  <td style={{ fontSize: 11, color: "var(--text-muted)" }}>
                    {entry.recorded_at
                      ? new Date(entry.recorded_at).toLocaleTimeString()
                      : "—"}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {entry.orchestration_type || entry.action || "—"}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {entry.machine_target ||
                      entry.machine ||
                      entry.target ||
                      "—"}
                  </td>
                  <td style={{ fontSize: 12 }}>
                    {entry.deploy_action ||
                      entry.workflow ||
                      entry.action ||
                      "—"}
                  </td>
                  <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {entry.requested_by || "—"}
                  </td>
                  <td>
                    <span
                      className="section-badge"
                      style={{
                        background:
                          entry.decision === "accepted"
                            ? "rgba(63,185,80,0.12)"
                            : "rgba(248,81,73,0.12)",
                        color:
                          entry.decision === "accepted"
                            ? "var(--accent-green)"
                            : "var(--accent-red)",
                      }}
                    >
                      {entry.decision || "—"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {dispatchModalOpen ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.5)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) setDispatchModalOpen(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") setDispatchModalOpen(false);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="dispatch-workflow-title"
            style={{
              background: "var(--bg-primary)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              padding: 24,
              width: 480,
              maxWidth: "90vw",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 20,
              }}
            >
              <span
                id="dispatch-workflow-title"
                style={{ fontWeight: 700, fontSize: 15 }}
              >
                Dispatch Workflow
              </span>
              <button
                className="btn"
                aria-label="Close dispatch dialog"
                onClick={() => setDispatchModalOpen(false)}
                style={{ padding: "2px 8px" }}
              >
                ✕
              </button>
            </div>
            <div
              style={{ display: "flex", flexDirection: "column", gap: 12 }}
            >
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    marginBottom: 4,
                    color: "var(--text-secondary)",
                  }}
                >
                  Repository
                </label>
                <input
                  className="input"
                  style={{ width: "100%" }}
                  placeholder="e.g. Repository_Management"
                  value={dispatchRepo}
                  onChange={(e) => setDispatchRepo(e.target.value)}
                />
              </div>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    marginBottom: 4,
                    color: "var(--text-secondary)",
                  }}
                >
                  Workflow file
                </label>
                <input
                  className="input"
                  style={{ width: "100%" }}
                  placeholder="e.g. ci-standard.yml"
                  value={dispatchWorkflow}
                  onChange={(e) => setDispatchWorkflow(e.target.value)}
                />
              </div>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    marginBottom: 4,
                    color: "var(--text-secondary)",
                  }}
                >
                  Branch / ref
                </label>
                <input
                  className="input"
                  style={{ width: "100%" }}
                  placeholder="main"
                  value={dispatchBranch}
                  onChange={(e) => setDispatchBranch(e.target.value)}
                />
              </div>
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    marginBottom: 4,
                    color: "var(--text-secondary)",
                  }}
                >
                  Machine target (optional)
                </label>
                <select
                  className="input"
                  style={{ width: "100%" }}
                  value={dispatchMachineTarget}
                  onChange={(e) => setDispatchMachineTarget(e.target.value)}
                >
                  <option value="">— any machine —</option>
                  {machines.map((m) => (
                    <option key={m.name} value={m.name}>
                      {m.display_name || m.name}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            {dispatchError ? (
              <div
                style={{
                  color: "var(--accent-red)",
                  fontSize: 12,
                  marginTop: 12,
                }}
              >
                {dispatchError}
              </div>
            ) : null}
            <div
              style={{
                display: "flex",
                gap: 8,
                marginTop: 20,
                justifyContent: "flex-end",
              }}
            >
              <button
                className="btn"
                onClick={() => setDispatchModalOpen(false)}
              >
                Cancel
              </button>
              <button
                className="btn btn-primary"
                onClick={handleDispatch}
                disabled={dispatchLoading}
              >
                {dispatchLoading ? "Dispatching…" : "Dispatch"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default FleetOrchestrationTab;
