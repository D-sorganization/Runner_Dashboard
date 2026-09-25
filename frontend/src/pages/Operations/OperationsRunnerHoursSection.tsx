import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";

export interface RunnerScheduleEntry {
  name: string;
  days?: string[];
  start: string;
  end: string;
  runners: number;
}

interface ScheduleConfig {
  schedules?: RunnerScheduleEntry[];
}

interface ScheduleState {
  desired?: number | null;
  online?: number | null;
  installed?: number | null;
  busy?: number | null;
  idle?: number | null;
  offline?: number | null;
  reason?: string;
  available?: boolean;
  error?: string;
  timestamp?: string | number;
}

export interface RunnerScheduleData {
  schedule?: ScheduleConfig;
  state?: ScheduleState;
  machine?: string;
  aliases?: string[];
  max_runners?: number;
  config_path?: string;
  timers?: Record<string, string>;
}

export interface OperationsRunnerHoursSectionProps {
  initialData?: RunnerScheduleData;
  onDataChange?: (data: RunnerScheduleData) => void;
}

export function OperationsRunnerHoursSection({
  initialData,
  onDataChange,
}: OperationsRunnerHoursSectionProps): React.ReactElement {
  const [data, setData] = useState<RunnerScheduleData>(initialData ?? {});
  const [loading, setLoading] = useState(initialData === undefined);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [entries, setEntries] = useState<RunnerScheduleEntry[]>([]);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    legacyFetch("/api/fleet/schedule", { signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<RunnerScheduleData>;
      })
      .then((payload: RunnerScheduleData | null) => {
        if (payload) {
          setData(payload);
          setEntries(payload.schedule?.schedules || []);
          onDataChange?.(payload);
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Failed to load runner hours");
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, [onDataChange]);

  useEffect(() => {
    if (initialData === undefined) {
      const controller = new AbortController();
      load(controller.signal);
      return () => controller.abort();
    } else {
      setEntries(initialData.schedule?.schedules || []);
    }
  }, [load, initialData]);

  const handleSave = (applyNow: boolean) => {
    setSaving(true);
    setError(null);
    legacyFetch("/api/fleet/schedule", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        schedules: entries,
        apply_now: applyNow,
      }),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`Save failed (HTTP ${r.status})`);
        return r.json() as Promise<RunnerScheduleData>;
      })
      .then((payload) => {
        if (payload) {
          setData(payload);
          setEntries(payload.schedule?.schedules || entries);
          onDataChange?.(payload);
        }
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Failed to save runner hours");
      })
      .finally(() => {
        setSaving(false);
      });
  };

  const state = data.state || {};
  const timers = data.timers || {};

  return (
    <section
      id="runner-hours"
      aria-labelledby="heading-runner-hours"
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
            id="heading-runner-hours"
            style={{
              fontSize: "1.25rem",
              fontWeight: 600,
              margin: "0 0 0.25rem 0",
              color: "var(--text-primary, #c9d1d9)",
            }}
          >
            Runner hours
          </h2>
          <p
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
              margin: 0,
            }}
          >
            Scheduled runner capacity windows, on/off timers, and desired-capacity state
          </p>
        </div>

        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <button
            type="button"
            onClick={() => load()}
            disabled={loading || saving}
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
            {loading ? "Refreshing..." : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => handleSave(false)}
            disabled={loading || saving}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 500,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
            }}
          >
            {saving ? "Saving..." : "Save Schedule"}
          </button>
          <button
            type="button"
            onClick={() => handleSave(true)}
            disabled={loading || saving}
            style={{
              padding: "0.4rem 0.75rem",
              fontSize: "0.8125rem",
              fontWeight: 600,
              borderRadius: "6px",
              cursor: "pointer",
              background: "var(--btn-primary-bg, #238636)",
              color: "var(--btn-primary-text, #ffffff)",
              border: "1px solid var(--border-green, #2ea043)",
            }}
          >
            Apply Now
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
          <span>Failed to load runner hours: {error}</span>
          <button
            type="button"
            onClick={() => load()}
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

      {/* Stats row */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
          gap: "0.75rem",
          marginBottom: "1rem",
        }}
      >
        <div
          style={{
            padding: "0.625rem 0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>DESIRED</div>
          <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
            {state.desired ?? 0} desired
          </div>
        </div>
        <div
          style={{
            padding: "0.625rem 0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>ONLINE</div>
          <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
            {state.online ?? 0} online
          </div>
        </div>
        <div
          style={{
            padding: "0.625rem 0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>BUSY</div>
          <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
            {state.busy ?? 0} busy
          </div>
        </div>
        <div
          style={{
            padding: "0.625rem 0.875rem",
            borderRadius: "6px",
            background: "var(--bg-tertiary, #21262d)",
            border: "1px solid var(--border-subtle, #30363d)",
          }}
        >
          <div style={{ fontSize: "0.75rem", color: "var(--text-muted, #8b949e)" }}>OFFLINE</div>
          <div style={{ fontSize: "1.125rem", fontWeight: 600, color: "var(--text-primary, #c9d1d9)" }}>
            {state.offline ?? 0} offline
          </div>
        </div>
      </div>

      {/* Windows table */}
      <div
        style={{
          border: "1px solid var(--border-color, #30363d)",
          borderRadius: "6px",
          overflow: "hidden",
          background: "var(--bg-tertiary, #21262d)",
          marginBottom: "1rem",
        }}
      >
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.875rem" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border-color, #30363d)", textAlign: "left" }}>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>WINDOW NAME</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>ACTIVE DAYS</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>HOURS</th>
              <th style={{ padding: "0.625rem 0.875rem", color: "var(--text-muted, #8b949e)" }}>TARGET RUNNERS</th>
            </tr>
          </thead>
          <tbody>
            {entries.length === 0 ? (
              <tr>
                <td colSpan={4} style={{ padding: "1.5rem", textAlign: "center", color: "var(--text-muted, #8b949e)" }}>
                  No runner schedule windows configured.
                </td>
              </tr>
            ) : (
              entries.map((entry, idx) => (
                <tr
                  key={idx}
                  style={{
                    borderBottom: idx < entries.length - 1 ? "1px solid var(--border-subtle, #30363d)" : "none",
                  }}
                >
                  <td style={{ padding: "0.625rem 0.875rem", fontWeight: 500, color: "var(--text-primary, #c9d1d9)" }}>
                    {entry.name}
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-secondary, #8b949e)" }}>
                    {(entry.days || []).join(", ").toUpperCase() || "ALL DAYS"}
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-primary, #c9d1d9)" }}>
                    {entry.start} - {entry.end}
                  </td>
                  <td style={{ padding: "0.625rem 0.875rem", color: "var(--text-primary, #c9d1d9)" }}>
                    <span
                      style={{
                        padding: "0.15rem 0.5rem",
                        borderRadius: "4px",
                        background: "var(--badge-neutral-bg, rgba(110,118,129,0.2))",
                        fontWeight: 600,
                      }}
                    >
                      {entry.runners}
                    </span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Footer info: timers and config path */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          fontSize: "0.75rem",
          color: "var(--text-muted, #8b949e)",
          paddingTop: "0.5rem",
          borderTop: "1px solid var(--border-subtle, #30363d)",
        }}
      >
        <div>
          <span>Config path: </span>
          <code style={{ color: "var(--text-secondary, #8b949e)" }}>{data.config_path || "system default"}</code>
        </div>
        <div>
          <span>Scheduler: {timers.scheduler || "active"} · Cleanup: {timers.cleanup || "active"}</span>
        </div>
      </div>
    </section>
  );
}

export default OperationsRunnerHoursSection;
