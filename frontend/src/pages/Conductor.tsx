import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Badge } from "../primitives/Badge";
import { EmptyState } from "../primitives/EmptyState";
import { SkeletonCard, SkeletonLine } from "../primitives/Skeleton";
import { TouchButton } from "../primitives/TouchButton";

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

const MODE_TONES: Record<QueueStatus["mode"], "success" | "warning" | "danger"> = {
  running: "success",
  paused: "warning",
  draining: "danger",
};

export function Conductor() {
  const [status, setStatus] = useState<QueueStatus | null>(null);
  const [loading, setLoading] = useState(true);
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
    setError(null);
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
        className="glass-card conductor conductor--loading"
      >
        <div className="conductor__muted">Loading Conductor...</div>
        <SkeletonLine height={18} width="45%" />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={2} />
      </div>
    );
  }

  if (disabled) {
    return (
      <div className="glass-card conductor">
        <EmptyState
          title="Conductor integration is disabled"
          description="Set DASHBOARD_ENABLE_CONDUCTOR=1 on the dashboard server to enable the orchestrator admission gate."
        />
      </div>
    );
  }

  if (error || !status) {
    return (
      <div className="glass-card conductor">
        <EmptyState
          variant="error"
          title="Failed to load Conductor status"
          description={error ?? "The queue status response was empty."}
          onRetry={load}
        />
      </div>
    );
  }

  const { mode, capacity, work, provider_mix, budget, active_leases, reserved_slots } = status;
  const providerEntries = Object.entries(provider_mix);
  const budgetPct = budget.limit_usd > 0 ? Math.min(100, (budget.spent_usd / budget.limit_usd) * 100) : 0;

  return (
    <div className="glass-card conductor">
      <div className="conductor__header">
        <h2 className="conductor__title">Conductor</h2>
        <Badge className="conductor__mode" tone={MODE_TONES[mode]} size="sm">
          {mode}
        </Badge>
      </div>

      {/* Work classification */}
      <div className="conductor__stats">
        <Stat testid="work-planned" label="Planned" value={work.planned} />
        <Stat testid="work-active" label="Active" value={work.active} />
        <Stat testid="work-blocked" label="Blocked" value={work.blocked} accent={work.blocked > 0} />
      </div>

      {/* Capacity */}
      <Section title="Fleet capacity">
        <div className="conductor__capacity">
          <span>Idle: <strong>{capacity.idle_runners}</strong></span>
          <span>Busy: <strong>{capacity.busy_runners}</strong></span>
          <span>Online: <strong>{capacity.online_runners}</strong></span>
          <span>Leases: <strong>{active_leases}</strong> ({reserved_slots} slots)</span>
        </div>
      </Section>

      {/* Provider mix */}
      <Section title="Provider mix">
        {providerEntries.length === 0 ? (
          <p className="conductor__muted">No active leases.</p>
        ) : (
          <div className="conductor__provider-list">
            {providerEntries.map(([provider, count]) => (
              <Badge key={provider} className="conductor__provider-badge" tone="neutral" size="sm">
                {provider}: {count}
              </Badge>
            ))}
          </div>
        )}
      </Section>

      {/* Budget burn */}
      <Section title="Budget burn">
        <div className="conductor__budget-copy">
          <strong>${budget.spent_usd.toFixed(2)}</strong>
          {budget.limit_usd > 0 ? ` / $${budget.limit_usd.toFixed(2)}` : " (no limit configured)"}
        </div>
        {budget.limit_usd > 0 && (
          <progress
            aria-label="Conductor budget burn"
            className={budgetPct > 90 ? "conductor__budget-meter conductor__budget-meter--danger" : "conductor__budget-meter"}
            max={100}
            value={budgetPct}
          />
        )}
      </Section>

      {/* Controls */}
      <Section title="Controls">
        <div className="conductor__controls">
          <TouchButton
            className="conductor__action-button"
            onClick={() => sendAction("pause")}
            disabled={busy || mode === "paused"}
          >
            Pause
          </TouchButton>
          <TouchButton
            className="conductor__action-button"
            onClick={() => sendAction("resume")}
            disabled={busy || mode === "running"}
            variant="primary"
          >
            Resume
          </TouchButton>
          <TouchButton
            className="conductor__action-button"
            onClick={() => sendAction("drain")}
            disabled={busy || mode === "draining"}
            variant="danger"
          >
            Drain
          </TouchButton>
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
    <div className={accent ? "conductor__stat conductor__stat--accent" : "conductor__stat"}>
      <div data-testid={testid} className="conductor__stat-value">
        {value}
      </div>
      <div className="conductor__stat-label">{label}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="conductor__section">
      <div className="conductor__section-title">{title}</div>
      {children}
    </section>
  );
}

export default Conductor;
