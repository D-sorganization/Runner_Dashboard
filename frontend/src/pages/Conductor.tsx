import { useCallback, useEffect, useState } from "react";
import { SkeletonCard, SkeletonLine } from "../primitives/Skeleton";

/**
 * Conductor tab — Repository_Management epic #1273, issue #1282.
 *
 * Minimal-but-real visibility surface for the Conductor orchestrator's
 * admission gate. Shows planned/active/blocked work, provider mix, budget burn,
 * and pause/resume/drain controls wired to `/api/orchestrator/queue`.
 *
 * Orthogonality: the whole tab is self-contained and degrades to an inert
 * notice when the backend feature flag is off (HTTP 404), so a failure here
 * cannot break other tabs.
 */

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

interface QueueStatus {
  enabled: boolean;
  mode: "running" | "paused" | "draining";
  active_leases: number;
  reserved_slots: number;
  capacity: Capacity;
  work: WorkSummary;
  provider_mix: Record<string, number>;
  budget: BudgetSummary;
}

type QueueAction = "pause" | "resume" | "drain";

const XHR_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

const MODE_COLORS: Record<QueueStatus["mode"], string> = {
  running: "var(--accent-green)",
  paused: "var(--accent-yellow, #eab308)",
  draining: "var(--accent-red)",
};

export function Conductor() {
  const [status, setStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    fetch("/api/orchestrator/queue", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then((r) => {
        if (r.status === 404) {
          setDisabled(true);
          setLoading(false);
          return null;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: QueueStatus | null) => {
        if (data) {
          setStatus(data);
          setDisabled(false);
        }
        setLoading(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const sendAction = useCallback((action: QueueAction) => {
    setBusy(true);
    fetch("/api/orchestrator/queue", {
      method: "POST",
      headers: XHR_HEADERS,
      body: JSON.stringify({ action }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: QueueStatus) => {
        setStatus(data);
        setBusy(false);
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
      });
  }, []);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading Conductor orchestrator status"
        className="glass-card"
        style={{ padding: "16px", margin: "16px", display: "flex", flexDirection: "column", gap: "12px" }}
      >
        <div style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Loading Conductor…</div>
        <SkeletonLine height={18} width="45%" />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={2} />
      </div>
    );
  }

  if (disabled) {
    return (
      <div className="glass-card" style={{ padding: "16px", margin: "16px" }}>
        <h2 style={{ fontSize: "16px", marginBottom: "8px" }}>Conductor</h2>
        <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
          Conductor integration is disabled. Set <code>DASHBOARD_ENABLE_CONDUCTOR=1</code> on the dashboard
          server to enable the orchestrator admission gate.
        </p>
      </div>
    );
  }

  if (error || !status) {
    return (
      <div className="glass-card" style={{ padding: "16px", margin: "16px" }}>
        <h2 style={{ fontSize: "16px", marginBottom: "8px" }}>Conductor</h2>
        <div
          style={{
            color: "var(--accent-red)",
            fontSize: "12px",
            padding: "8px",
            background: "rgba(239, 68, 68, 0.08)",
            borderRadius: "4px",
          }}
        >
          Failed to load Conductor status{error ? `: ${error}` : ""}.
        </div>
        <button onClick={load} className="btn btn-sm btn-blue" style={{ marginTop: "8px", fontSize: "12px" }}>
          Retry
        </button>
      </div>
    );
  }

  const { mode, capacity, work, provider_mix, budget, active_leases, reserved_slots } = status;
  const providerEntries = Object.entries(provider_mix);
  const budgetPct = budget.limit_usd > 0 ? Math.min(100, (budget.spent_usd / budget.limit_usd) * 100) : 0;

  return (
    <div className="glass-card" style={{ padding: "16px", margin: "16px" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
        <h2 style={{ fontSize: "16px", margin: 0 }}>Conductor</h2>
        <span
          style={{
            fontSize: "12px",
            fontWeight: 600,
            color: MODE_COLORS[mode],
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          {mode}
        </span>
      </div>

      {/* Work classification */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "16px" }}>
        <Stat testid="work-planned" label="Planned" value={work.planned} />
        <Stat testid="work-active" label="Active" value={work.active} />
        <Stat testid="work-blocked" label="Blocked" value={work.blocked} accent={work.blocked > 0} />
      </div>

      {/* Capacity */}
      <Section title="Fleet capacity">
        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", fontSize: "13px" }}>
          <span>Idle: <strong>{capacity.idle_runners}</strong></span>
          <span>Busy: <strong>{capacity.busy_runners}</strong></span>
          <span>Online: <strong>{capacity.online_runners}</strong></span>
          <span>Leases: <strong>{active_leases}</strong> ({reserved_slots} slots)</span>
        </div>
      </Section>

      {/* Provider mix */}
      <Section title="Provider mix">
        {providerEntries.length === 0 ? (
          <p style={{ fontSize: "12px", color: "var(--text-secondary)", margin: 0 }}>No active leases.</p>
        ) : (
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {providerEntries.map(([provider, count]) => (
              <span
                key={provider}
                style={{
                  fontSize: "12px",
                  padding: "3px 8px",
                  borderRadius: "999px",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                }}
              >
                {provider}: {count}
              </span>
            ))}
          </div>
        )}
      </Section>

      {/* Budget burn */}
      <Section title="Budget burn">
        <div style={{ fontSize: "13px", marginBottom: "6px" }}>
          <strong>${budget.spent_usd.toFixed(2)}</strong>
          {budget.limit_usd > 0 ? ` / $${budget.limit_usd.toFixed(2)}` : " (no limit configured)"}
        </div>
        {budget.limit_usd > 0 && (
          <div style={{ height: "6px", background: "var(--bg-secondary)", borderRadius: "999px", overflow: "hidden" }}>
            <div
              style={{
                height: "100%",
                width: `${budgetPct}%`,
                background: budgetPct > 90 ? "var(--accent-red)" : "var(--accent-green)",
              }}
            />
          </div>
        )}
      </Section>

      {/* Controls */}
      <Section title="Controls">
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
          <button
            onClick={() => sendAction("pause")}
            disabled={busy || mode === "paused"}
            className="btn btn-sm"
            style={{ fontSize: "12px" }}
          >
            Pause
          </button>
          <button
            onClick={() => sendAction("resume")}
            disabled={busy || mode === "running"}
            className="btn btn-sm btn-blue"
            style={{ fontSize: "12px" }}
          >
            Resume
          </button>
          <button
            onClick={() => sendAction("drain")}
            disabled={busy || mode === "draining"}
            className="btn btn-sm btn-red"
            style={{ fontSize: "12px" }}
          >
            Drain
          </button>
        </div>
      </Section>
    </div>
  );
}

function Stat({
  label,
  value,
  testid,
  accent = false,
}: {
  label: string;
  value: number;
  testid: string;
  accent?: boolean;
}) {
  return (
    <div
      style={{
        flex: 1,
        padding: "10px",
        border: "1px solid var(--border)",
        borderRadius: "6px",
        background: "var(--bg-secondary)",
        textAlign: "center",
      }}
    >
      <div
        data-testid={testid}
        style={{ fontSize: "20px", fontWeight: 700, color: accent ? "var(--accent-red)" : "var(--text-primary)" }}
      >
        {value}
      </div>
      <div style={{ fontSize: "11px", color: "var(--text-secondary)", textTransform: "uppercase" }}>{label}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: "16px" }}>
      <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "8px" }}>{title}</div>
      {children}
    </div>
  );
}

export default Conductor;
