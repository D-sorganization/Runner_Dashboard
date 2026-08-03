// QueueTab – desktop Queue Health view.
//
// Migrated off the legacy `const h = React.createElement` / pervasive `any`
// pattern to typed TSX (issue #841). The legacy style is restricted to
// `frontend/src/legacy/` per CLAUDE.md; this module must not reintroduce it.
//
// Behaviour, DOM structure, and the public `QueueTab` prop contract are
// preserved so existing consumers (legacy/App.tsx, router.tsx) and the
// QueueTab test suite are unchanged.

import { useEffect, useState, type CSSProperties } from "react";

import { Badge } from "../../primitives/Badge";
import { Collapse } from "../../components/Collapse";
import { Stat } from "../../components/Stat";
import { SortTh } from "../../components/SortTh";
import { useToast } from "../../primitives/Toaster";
import { formatDuration } from "../../components/formatters";

import { DiagnosePanel } from "./DiagnosePanel";
import { StaleCleanupPanel } from "./StaleCleanupPanel";
import {
  sortRows,
  type CancelMap,
  type CancelState,
  type DiagnosePayload,
  type InlineMessage,
  type QueuePayload,
  type RunAccessors,
  type SortState,
  type WorkflowRun,
} from "./types";

export interface QueueTabProps {
  queue?: QueuePayload;
  loading?: boolean;
  onRefresh?: () => void;
}

const CONFIRM_TIMEOUT_MS = 5000;
const REFRESH_DELAY_MS = 1500;

// ── Per-run accessors used for column sorting ────────────────────────────────

function runRepo(run: WorkflowRun): string {
  return run.repository?.name ?? "";
}
function runRunner(run: WorkflowRun): string {
  return run.runner_name ?? run.runner?.name ?? "-";
}
function elapsedSeconds(run: WorkflowRun): number {
  const start = run.run_started_at ?? run.created_at;
  return start ? Math.round((Date.now() - new Date(start).getTime()) / 1000) : 0;
}
function waitingSeconds(run: WorkflowRun): number {
  return run.created_at
    ? Math.round((Date.now() - new Date(run.created_at).getTime()) / 1000)
    : 0;
}

const RUN_ACCESSORS: RunAccessors = {
  workflow: (run) => run.name,
  repo: runRepo,
  branch: (run) => run.head_branch,
  runner: runRunner,
  runningFor: elapsedSeconds,
  waiting: waitingSeconds,
};

function elapsedLabel(run: WorkflowRun): string {
  const start = run.run_started_at ?? run.created_at;
  if (!start) return "-";
  return formatDuration(
    Math.round((Date.now() - new Date(start).getTime()) / 1000),
  );
}
function waitedLabel(run: WorkflowRun): string {
  if (!run.created_at) return "-";
  return formatDuration(
    Math.round((Date.now() - new Date(run.created_at).getTime()) / 1000),
  );
}
function waitColor(run: WorkflowRun): string {
  const s = Math.round(
    (Date.now() - new Date(run.created_at ?? 0).getTime()) / 1000,
  );
  return s > 300
    ? "var(--accent-red)"
    : s > 60
      ? "var(--accent-yellow)"
      : "inherit";
}

const cancelButtonBase: CSSProperties = {
  fontSize: 11,
  padding: "2px 7px",
  border: "1px solid var(--accent-red)",
  borderRadius: 4,
};

interface CancelButtonProps {
  cstate: CancelState | undefined;
  isConfirming: boolean;
  confirmingLabel: string;
  onClick: () => void;
}

/** Two-step destructive cancel button shared across the run tables (issue #7). */
function CancelButton({
  cstate,
  isConfirming,
  confirmingLabel,
  onClick,
}: CancelButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={!!cstate}
      style={{
        ...cancelButtonBase,
        background: isConfirming ? "var(--accent-red)" : "none",
        color:
          cstate === "done"
            ? "var(--text-muted)"
            : isConfirming
              ? "#fff"
              : "var(--accent-red)",
        cursor: cstate ? "default" : "pointer",
      }}
    >
      {cstate === "pending" ? (
        <span className="spinner" />
      ) : cstate === "done" ? (
        "cancelled"
      ) : isConfirming ? (
        confirmingLabel
      ) : (
        "Cancel"
      )}
    </button>
  );
}

export function QueueTab(p: QueueTabProps) {
  // useToast is retained for parity with the prior implementation (the toast
  // surface is wired by the shell); the cancel flows use inline messaging.
  useToast();

  const [localQueue, setLocalQueue] = useState<QueuePayload | null>(null);
  const [localLoading, setLocalLoading] = useState(false);

  const [diag, setDiag] = useState<DiagnosePayload | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [cancelling, setCancelling] = useState<CancelMap>({});
  // Two-step inline confirmation state for destructive actions (issue #7)
  const [confirmWorkflow, setConfirmWorkflow] = useState<string | null>(null);
  const [confirmRun, setConfirmRun] = useState<string | null>(null);
  // Inline status message replaces alert() (issue #51)
  const [cancelMsg, setCancelMsg] = useState<InlineMessage | null>(null);
  const [ipSort, setIpSort] = useState<SortState>({
    key: "runningFor",
    dir: "desc",
  });
  const [queueSort, setQueueSort] = useState<SortState>({
    key: "waiting",
    dir: "desc",
  });

  function fetchLocalQueue(): void {
    setLocalLoading(true);
    fetch("/api/queue")
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then((data: QueuePayload) => {
        setLocalQueue(data);
        setLocalLoading(false);
      })
      .catch((err) => {
        // eslint-disable-next-line no-console
        console.error(err);
        setLocalLoading(false);
      });
  }

  useEffect(() => {
    if (!p.queue) {
      fetchLocalQueue();
    }
  }, [p.queue]);

  const q: QueuePayload = p.queue ?? localQueue ?? {};
  const loading = p.queue ? p.loading : localLoading;
  const onRefresh = p.onRefresh ?? (p.queue ? undefined : fetchLocalQueue);

  const ip = q.in_progress ?? [];
  const qu = q.queued ?? [];

  const sortedIp = sortRows(ip, ipSort, RUN_ACCESSORS);
  const sortedQu = sortRows(qu, queueSort, RUN_ACCESSORS);
  const staleQu = sortedQu.filter((r) => waitingSeconds(r) > 300);

  function runDiagnose(): void {
    setDiagLoading(true);
    setDiag(null);
    fetch("/api/queue/diagnose")
      .then((r) => r.json())
      .then((d: DiagnosePayload) => {
        setDiag(d);
        setDiagLoading(false);
      })
      .catch(() => {
        setDiagLoading(false);
      });
  }

  function setCancelKey(key: string, state: CancelState): void {
    setCancelling((prev) => ({ ...prev, [key]: state }));
  }

  function cancelRun(repo: string, runId: WorkflowRun["id"]): void {
    const key = repo + "/" + runId;
    setCancelKey(key, "pending");
    fetch("/api/runs/" + repo + "/cancel/" + runId, {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r) => r.json())
      .then(() => {
        setCancelKey(key, "done");
        if (onRefresh) setTimeout(onRefresh, REFRESH_DELAY_MS);
      })
      .catch(() => {
        setCancelKey(key, "error");
      });
  }

  /** Arms the two-step confirmation, or fires `cancelRun` on the second click. */
  function requestCancelRun(repo: string, runId: WorkflowRun["id"]): void {
    const rkey = repo + "/" + runId;
    if (confirmRun !== rkey) {
      setConfirmRun(rkey);
      setTimeout(() => {
        setConfirmRun((cur) => (cur === rkey ? null : cur));
      }, CONFIRM_TIMEOUT_MS);
    } else {
      setConfirmRun(null);
      cancelRun(repo, runId);
    }
  }

  function cancelWorkflow(workflowName: string, repo?: string): void {
    // Two-step inline confirmation: first call arms, second call fires (issue #7)
    const key = workflowName + (repo ? "/" + repo : "");
    if (confirmWorkflow !== key) {
      setConfirmWorkflow(key);
      setTimeout(() => {
        setConfirmWorkflow((cur) => (cur === key ? null : cur));
      }, CONFIRM_TIMEOUT_MS);
      return;
    }
    setConfirmWorkflow(null);
    fetch("/api/queue/cancel-workflow", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ workflow_name: workflowName, repo: repo ?? null }),
    })
      .then((r) => r.json())
      .then((d: { cancelled_count?: number; errors?: string[] }) => {
        const msg =
          "Cancelled " +
          (d.cancelled_count ?? 0) +
          " run(s)" +
          (d.errors && d.errors.length > 0
            ? " — Errors: " + d.errors.join(", ")
            : "");
        setCancelMsg({ type: "success", text: msg });
        setTimeout(() => setCancelMsg(null), 6000);
        if (onRefresh) setTimeout(onRefresh, REFRESH_DELAY_MS);
      })
      .catch(() => {
        setCancelMsg({ type: "error", text: "Cancel request failed" });
        setTimeout(() => setCancelMsg(null), 6000);
      });
  }

  // Group queued runs by workflow name for bulk-cancel
  const workflowGroups: Record<string, WorkflowRun[]> = {};
  qu.forEach((r) => {
    const n = r.name ?? "?";
    (workflowGroups[n] ??= []).push(r);
  });
  const bulkTargets = Object.keys(workflowGroups).filter(
    (n) => workflowGroups[n].length > 1,
  );

  return (
    <div>
      {cancelMsg ? (
        <div
          role="alert"
          style={{
            margin: "0 0 12px",
            padding: "10px 16px",
            borderRadius: 6,
            background:
              cancelMsg.type === "error"
                ? "rgba(248,81,73,0.15)"
                : "rgba(63,185,80,0.15)",
            color:
              cancelMsg.type === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)",
            border:
              "1px solid " +
              (cancelMsg.type === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)"),
            fontSize: 13,
          }}
        >
          {cancelMsg.text}
        </div>
      ) : null}

      <div className="stat-row">
        <Stat
          label="In Progress"
          value={ip.length}
          color={ip.length > 0 ? "var(--accent-yellow)" : "inherit"}
          sub={ip.length > 0 ? "actively running" : "idle"}
        />
        <Stat
          label="Queued"
          value={qu.length}
          color={qu.length > 0 ? "var(--accent-blue)" : "inherit"}
          sub={qu.length > 0 ? "waiting for runner" : "empty"}
        />
        <Stat label="Total Active" value={q.total ?? 0} sub="across all repos" />
        <Stat label="Auto-refresh" value="15s" sub="updates automatically" />
      </div>

      {q.stats?.complete === false ? (
        <div className="alert alert-warning" role="status" style={{ marginBottom: 12 }}>
          Queue data is incomplete: {q.stats.repos_failed ?? 0} of {q.stats.repos_sampled ?? 0}{" "}
          repositories were unavailable. Counts are a lower bound; an empty result does not mean all
          runners are idle. Source: {q.data_source ?? "unavailable"}
          {q.generated_at ? `; data generated ${new Date(q.generated_at).toLocaleString()}` : ""}.
        </div>
      ) : null}

      <div className="mobile-kpi-strip" aria-label="Queue health summary">
        {[
          { label: "Queued", value: qu.length },
          { label: "Running", value: ip.length },
          { label: "Stale", value: staleQu.length },
        ].map((item) => (
          <div key={item.label} className="mobile-kpi">
            <div className="mobile-kpi-label">{item.label}</div>
            <div className="mobile-kpi-value">{item.value}</div>
          </div>
        ))}
      </div>

      <StaleCleanupPanel onRefresh={onRefresh} />

      {qu.length > 0 ? (
        <div style={{ padding: "0 0 12px 0" }}>
          <button
            className="btn"
            onClick={runDiagnose}
            disabled={diagLoading}
            style={{ marginRight: 8 }}
          >
            {diagLoading ? <span className="spinner" /> : "🔍"} Why are jobs
            waiting?
          </button>
          {diag && <DiagnosePanel diag={diag} />}
        </div>
      ) : null}

      {loading && ip.length === 0 && qu.length === 0 ? (
        <div
          style={{
            textAlign: "center",
            padding: 40,
            color: "var(--text-muted)",
          }}
        >
          <span className="spinner" /> Loading queue...
        </div>
      ) : null}

      <Collapse
        title="In Progress"
        icon={<span className="queue-dot active" style={{ marginRight: 4 }} />}
        badge={ip.length + " running"}
        defaultOpen
      >
        {ip.length > 0 ? (
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th></th>
                  <SortTh
                    label="Workflow"
                    sortKey="workflow"
                    sort={ipSort}
                    setSort={setIpSort}
                  />
                  <SortTh
                    label="Repo"
                    sortKey="repo"
                    sort={ipSort}
                    setSort={setIpSort}
                  />
                  <SortTh
                    label="Branch"
                    sortKey="branch"
                    sort={ipSort}
                    setSort={setIpSort}
                  />
                  <SortTh
                    label="Runner"
                    sortKey="runner"
                    sort={ipSort}
                    setSort={setIpSort}
                  />
                  <SortTh
                    label="Running for"
                    sortKey="runningFor"
                    sort={ipSort}
                    setSort={setIpSort}
                  />
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {sortedIp.map((r) => {
                  const repo = runRepo(r);
                  const key = repo + "/" + r.id;
                  return (
                    <tr key={r.id}>
                      <td>
                        <Badge tone="warning">running</Badge>
                      </td>
                      <td>{r.name}</td>
                      <td>{repo}</td>
                      <td style={{ color: "var(--text-secondary)" }}>
                        {r.head_branch}
                      </td>
                      <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                        {runRunner(r)}
                      </td>
                      <td>{elapsedLabel(r)}</td>
                      <td
                        style={{
                          display: "flex",
                          gap: 6,
                          alignItems: "center",
                        }}
                      >
                        <a
                          href={r.html_url}
                          target="_blank"
                          rel="noopener"
                          style={{
                            color: "var(--accent-blue)",
                            textDecoration: "none",
                            fontSize: 12,
                          }}
                        >
                          View
                        </a>
                        {repo ? (
                          <CancelButton
                            cstate={cancelling[key]}
                            isConfirming={confirmRun === key}
                            confirmingLabel="Click again to confirm"
                            onClick={() => requestCancelRun(repo, r.id)}
                          />
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div
            style={{
              color: "var(--text-muted)",
              padding: 20,
              textAlign: "center",
            }}
          >
            No runs currently in progress
          </div>
        )}
      </Collapse>

      <Collapse
        title="Queued"
        icon={<span className="queue-dot waiting" style={{ marginRight: 4 }} />}
        badge={qu.length + " waiting"}
        defaultOpen
      >
        {qu.length > 0 ? (
          <div>
            {bulkTargets.length > 0 ? (
              <div
                style={{
                  padding: "8px 0",
                  display: "flex",
                  gap: 8,
                  flexWrap: "wrap",
                  alignItems: "center",
                }}
              >
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Bulk cancel:
                </span>
                {bulkTargets.map((name) => {
                  const isPending = confirmWorkflow === name;
                  return (
                    <button
                      key={name}
                      onClick={() => cancelWorkflow(name)}
                      style={{
                        fontSize: 11,
                        padding: "3px 10px",
                        background: isPending ? "var(--accent-red)" : "none",
                        border: isPending
                          ? "1px solid var(--accent-red)"
                          : "1px solid var(--accent-orange)",
                        color: isPending ? "#fff" : "var(--accent-orange)",
                        borderRadius: 4,
                        cursor: "pointer",
                      }}
                    >
                      {isPending ? (
                        "Click again to confirm"
                      ) : (
                        <>
                          Cancel all &apos;{name}&apos; (
                          {workflowGroups[name].length})
                        </>
                      )}
                    </button>
                  );
                })}
              </div>
            ) : null}

            <div className="queue-desktop-table" style={{ overflowX: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <SortTh
                      label="Workflow"
                      sortKey="workflow"
                      sort={queueSort}
                      setSort={setQueueSort}
                    />
                    <SortTh
                      label="Repo"
                      sortKey="repo"
                      sort={queueSort}
                      setSort={setQueueSort}
                    />
                    <SortTh
                      label="Branch"
                      sortKey="branch"
                      sort={queueSort}
                      setSort={setQueueSort}
                    />
                    <SortTh
                      label="Waiting"
                      sortKey="waiting"
                      sort={queueSort}
                      setSort={setQueueSort}
                    />
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {sortedQu.map((r, idx) => {
                    const repo = runRepo(r);
                    const key = repo + "/" + r.id;
                    const cstate = cancelling[key];
                    return (
                      <tr
                        key={r.id}
                        style={{ opacity: cstate === "done" ? 0.4 : 1 }}
                      >
                        <td
                          style={{
                            color: "var(--text-muted)",
                            fontVariantNumeric: "tabular-nums",
                            fontSize: 12,
                          }}
                        >
                          {idx + 1}
                        </td>
                        <td>{r.name}</td>
                        <td>{repo}</td>
                        <td style={{ color: "var(--text-secondary)" }}>
                          {r.head_branch}
                        </td>
                        <td
                          style={{
                            color: waitColor(r),
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {waitedLabel(r)}
                        </td>
                        <td
                          style={{
                            display: "flex",
                            gap: 6,
                            alignItems: "center",
                          }}
                        >
                          <a
                            href={r.html_url}
                            target="_blank"
                            rel="noopener"
                            style={{
                              color: "var(--accent-blue)",
                              textDecoration: "none",
                              fontSize: 12,
                            }}
                          >
                            View
                          </a>
                          {repo ? (
                            <CancelButton
                              cstate={cstate}
                              isConfirming={confirmRun === key}
                              confirmingLabel="Confirm cancel (1)"
                              onClick={() => requestCancelRun(repo, r.id)}
                            />
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mobile-card-list" aria-label="Stale queued runs">
              {(staleQu.length > 0 ? staleQu : sortedQu).map((r) => {
                const repo = runRepo(r);
                const key = repo + "/" + r.id;
                const cstate = cancelling[key];
                const isConfirming = confirmRun === key;
                return (
                  <div
                    key={"mobile-" + r.id}
                    className="mobile-run-card"
                    style={{ opacity: cstate === "done" ? 0.45 : 1 }}
                  >
                    <div className="mobile-run-title">
                      <span className="queue-dot waiting" />
                      <span className="mobile-run-name">{r.name ?? "?"}</span>
                    </div>
                    <div className="mobile-run-meta">
                      <span>{repo || "unknown repo"}</span>
                      <span>{r.head_branch || "unknown branch"}</span>
                      <span style={{ color: waitColor(r) }}>{waitedLabel(r)}</span>
                    </div>
                    <div className="mobile-run-actions">
                      <a
                        href={r.html_url}
                        target="_blank"
                        rel="noopener"
                        style={{
                          color: "var(--accent-blue)",
                          textDecoration: "none",
                          fontSize: 12,
                        }}
                      >
                        View run
                      </a>
                      {repo ? (
                        <button
                          className="btn"
                          onClick={() => requestCancelRun(repo, r.id)}
                          disabled={!!cstate}
                          style={{
                            background: isConfirming
                              ? "var(--accent-red)"
                              : "var(--bg-secondary)",
                            border: "1px solid var(--accent-red)",
                            color: isConfirming ? "#fff" : "var(--accent-red)",
                          }}
                        >
                          {cstate === "pending" ? (
                            <span className="spinner" />
                          ) : cstate === "done" ? (
                            "Cancelled"
                          ) : isConfirming ? (
                            "Confirm cancel (1)"
                          ) : (
                            "Cancel"
                          )}
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div
            style={{
              color: "var(--text-muted)",
              padding: 20,
              textAlign: "center",
            }}
          >
            {q.stats?.complete === false
              ? "No active jobs were visible in the partial sample"
              : "Queue is empty — all runners idle"}
          </div>
        )}
      </Collapse>
    </div>
  );
}

export { QueueMobile } from "./Mobile";
