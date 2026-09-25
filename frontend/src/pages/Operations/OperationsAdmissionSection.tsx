import React, { useCallback, useEffect, useState } from "react";

interface WorkSummary {
  planned: number;
  active: number;
  blocked: number;
}

interface BudgetSummary {
  spent_usd: number;
  limit_usd: number;
}

interface Capacity {
  idle_runners: number;
  online_runners: number;
  busy_runners: number;
  total_runners: number;
}

export interface QueueStatus {
  enabled: boolean;
  mode: "running" | "paused" | "draining";
  active_leases: number;
  reserved_slots: number;
  capacity: Capacity;
  work: WorkSummary;
  provider_mix: Record<string, number>;
  budget: BudgetSummary;
}

export type QueueAction = "pause" | "resume" | "drain";

export interface OperationsAdmissionSectionProps {
  initialStatus?: QueueStatus | null;
  onStatusChange?: (status: QueueStatus | null) => void;
}

const XHR_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

export function OperationsAdmissionSection({
  initialStatus,
  onStatusChange,
}: OperationsAdmissionSectionProps): React.ReactElement {
  const [status, setStatus] = useState<QueueStatus | null>(initialStatus ?? null);
  const [loading, setLoading] = useState(initialStatus === undefined);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetch("/api/orchestrator/queue", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then((r) => {
        if (r.status === 404) {
          setDisabled(true);
          setStatus(null);
          onStatusChange?.(null);
          return null;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<QueueStatus>;
      })
      .then((data: QueueStatus | null) => {
        if (data) {
          setStatus(data);
          setDisabled(false);
          onStatusChange?.(data);
        }
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        setLoading(false);
      });
  }, [onStatusChange]);

  useEffect(() => {
    if (initialStatus === undefined) {
      load();
    }
  }, [load, initialStatus]);

  const sendAction = useCallback(
    (action: QueueAction) => {
      setBusy(true);
      fetch("/api/orchestrator/queue", {
        method: "POST",
        headers: XHR_HEADERS,
        body: JSON.stringify({ action }),
      })
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json() as Promise<QueueStatus>;
        })
        .then((next) => {
          setStatus(next);
          onStatusChange?.(next);
        })
        .catch((e: unknown) => {
          setError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          setBusy(false);
        });
    },
    [onStatusChange],
  );

  const mode = status?.mode ?? "running";
  const modeTone =
    mode === "running"
      ? "var(--badge-success-bg, rgba(46,160,67,0.15))"
      : mode === "paused"
        ? "var(--badge-warning-bg, rgba(210,153,34,0.15))"
        : "var(--badge-danger-bg, rgba(248,81,73,0.15))";
  const modeColor =
    mode === "running"
      ? "var(--badge-success-text, #3fb950)"
      : mode === "paused"
        ? "var(--badge-warning-text, #d29922)"
        : "var(--badge-danger-text, #f85149)";

  return (
    <section
      id="admission"
      aria-labelledby="heading-admission"
      style={{
        padding: "1.25rem",
        marginBottom: "1.5rem",
        borderRadius: "8px",
        background: "var(--bg-secondary, #161b22)",
        border: "1px solid var(--border-color, #30363d)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: "1rem",
          marginBottom: "1rem",
        }}
      >
        <div>
          <h2
            id="heading-admission"
            style={{
              fontSize: "1.25rem",
              fontWeight: 600,
              margin: "0 0 0.25rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Admission (Conductor)
          </h2>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
              margin: 0,
            }}
          >
            Work admission gate, queue controls, and budget limits
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            onClick={load}
            disabled={loading || busy}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-secondary, #8b949e)",
              border: "1px solid var(--border-subtle, #30363d)",
            }}
          >
            {loading ? "Refreshing..." : "Refresh Gate"}
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            marginBottom: "1rem",
            borderRadius: "6px",
            background: "var(--bg-danger-subtle, rgba(248,81,73,0.1))",
            border: "1px solid var(--border-danger, #f85149)",
            color: "var(--text-danger, #f85149)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>Failed to load admission queue: {error}</span>
          <button
            type="button"
            onClick={load}
            style={{
              padding: "0.25rem 0.6rem",
              fontSize: "0.75rem",
              fontWeight: 600,
              borderRadius: "4px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
            }}
          >
            Retry
          </button>
        </div>
      )}

      {disabled ? (
        <div
          style={{
            padding: "2rem",
            textAlign: "center",
            background: "var(--bg-tertiary, #21262d)",
            borderRadius: "6px",
            border: "1px dashed var(--border-subtle, #30363d)",
            color: "var(--text-muted, #8b949e)",
          }}
        >
          <p style={{ margin: "0 0 0.5rem 0", fontWeight: 600 }}>Conductor admission gate is not enabled</p>
          <p style={{ margin: 0, fontSize: "0.8125rem" }}>
            The orchestrator admission service is not active in this environment (HTTP 404).
          </p>
        </div>
      ) : status ? (
        <div>
          {/* Header controls & status pills */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "1rem",
              padding: "0.875rem",
              background: "var(--bg-tertiary, #21262d)",
              borderRadius: "6px",
              marginBottom: "1rem",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
              <span
                style={{
                  fontSize: "0.8125rem",
                  fontWeight: 600,
                  padding: "0.2rem 0.6rem",
                  borderRadius: "4px",
                  background: modeTone,
                  color: modeColor,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                {status.mode}
              </span>
              <span style={{ fontSize: "0.875rem", color: "var(--text-primary, #c9d1d9)" }}>
                {status.active_leases} active leases
              </span>
              <span style={{ fontSize: "0.8125rem", color: "var(--text-muted, #8b949e)" }}>
                ({status.reserved_slots} reserved slots)
              </span>
            </div>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              {status.mode !== "running" && (
                <button
                  type="button"
                  onClick={() => sendAction("resume")}
                  disabled={busy}
                  style={{
                    padding: "0.35rem 0.75rem",
                    fontSize: "0.8125rem",
                    fontWeight: 500,
                    borderRadius: "6px",
                    cursor: "pointer",
                    background: "var(--badge-success-bg, rgba(46,160,67,0.15))",
                    color: "var(--badge-success-text, #3fb950)",
                    border: "1px solid var(--border-green, #2ea043)",
                  }}
                >
                  Resume Admission
                </button>
              )}
              {status.mode !== "paused" && (
                <button
                  type="button"
                  onClick={() => sendAction("pause")}
                  disabled={busy}
                  style={{
                    padding: "0.35rem 0.75rem",
                    fontSize: "0.8125rem",
                    fontWeight: 500,
                    borderRadius: "6px",
                    cursor: "pointer",
                    background: "var(--badge-warning-bg, rgba(210,153,34,0.15))",
                    color: "var(--badge-warning-text, #d29922)",
                    border: "1px solid var(--border-yellow, #d29922)",
                  }}
                >
                  Pause Admission
                </button>
              )}
              {status.mode !== "draining" && (
                <button
                  type="button"
                  onClick={() => sendAction("drain")}
                  disabled={busy}
                  style={{
                    padding: "0.35rem 0.75rem",
                    fontSize: "0.8125rem",
                    fontWeight: 500,
                    borderRadius: "6px",
                    cursor: "pointer",
                    background: "var(--badge-danger-bg, rgba(248,81,73,0.15))",
                    color: "var(--badge-danger-text, #f85149)",
                    border: "1px solid var(--border-red, #da3633)",
                  }}
                >
                  Drain Queue
                </button>
              )}
            </div>
          </div>

          {/* Metrics grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: "1rem",
            }}
          >
            {/* Work Queue */}
            <div
              style={{
                padding: "0.875rem",
                borderRadius: "6px",
                background: "var(--bg-tertiary, #21262d)",
                border: "1px solid var(--border-subtle, #30363d)",
              }}
            >
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)", marginBottom: "0.4rem" }}>
                WORK QUEUE
              </div>
              <div style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
                <span>{status.work.planned} planned</span>
                <span style={{ fontSize: "0.8125rem", fontWeight: 400, marginLeft: "0.5rem", color: "var(--text-muted, #8b949e)" }}>
                  ({status.work.active} active, {status.work.blocked} blocked)
                </span>
              </div>
            </div>

            {/* Runner Capacity */}
            <div
              style={{
                padding: "0.875rem",
                borderRadius: "6px",
                background: "var(--bg-tertiary, #21262d)",
                border: "1px solid var(--border-subtle, #30363d)",
              }}
            >
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)", marginBottom: "0.4rem" }}>
                RUNNER CAPACITY
              </div>
              <div style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
                <span>{status.capacity.idle_runners} idle</span>
                <span style={{ fontSize: "0.8125rem", fontWeight: 400, marginLeft: "0.5rem", color: "var(--text-muted, #8b949e)" }}>
                  ({status.capacity.busy_runners} busy / {status.capacity.total_runners} total)
                </span>
              </div>
            </div>

            {/* Budget Burn */}
            <div
              style={{
                padding: "0.875rem",
                borderRadius: "6px",
                background: "var(--bg-tertiary, #21262d)",
                border: "1px solid var(--border-subtle, #30363d)",
              }}
            >
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)", marginBottom: "0.4rem" }}>
                BUDGET BURN
              </div>
              <div style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
                ${status.budget.spent_usd.toFixed(2)} / ${status.budget.limit_usd.toFixed(2)}
              </div>
            </div>

            {/* Provider Mix */}
            <div
              style={{
                padding: "0.875rem",
                borderRadius: "6px",
                background: "var(--bg-tertiary, #21262d)",
                border: "1px solid var(--border-subtle, #30363d)",
              }}
            >
              <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)", marginBottom: "0.4rem" }}>
                PROVIDER MIX
              </div>
              <div style={{ fontSize: "0.875rem", color: "var(--text-primary, #c9d1d9)" }}>
                {Object.entries(status.provider_mix).length === 0 ? (
                  <span style={{ color: "var(--text-muted, #8b949e)" }}>None</span>
                ) : (
                  Object.entries(status.provider_mix)
                    .map(([provider, count]) => `${provider}: ${count}`)
                    .join(", ")
                )}
              </div>
            </div>
          </div>
        </div>
      ) : loading ? (
        <div style={{ padding: "1.5rem", textAlign: "center", color: "var(--text-muted, #8b949e)" }}>
          Loading admission gate status...
        </div>
      ) : null}
    </section>
  );
}

export default OperationsAdmissionSection;
