/**
 * RunnerSchedule.tsx — the "Runner Schedule" tab, extracted (behaviour-wise
 * 1:1) from the legacy `App.tsx` monolith as part of the decomposition epic
 * (#836, pass 6).
 *
 * Shows the local machine's runner-capacity schedule: a stat row (desired /
 * online / busy / offline counts), an editable table of per-window schedule
 * entries (days, start, end, runner count), and Save / Apply-Now controls that
 * call back into the legacy App. A short footer summarises the scheduler/cleanup
 * systemd timer state and config path.
 *
 * Presentational: the capacity `data` (and its poll) is owned by the legacy
 * App, so this page receives the already-fetched `data`, a `loading` flag, and
 * `onRefresh` / `onSave` callbacks. The edited draft schedule is local state,
 * re-seeded from props whenever the config path or state timestamp changes —
 * matching the original legacy render exactly.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { Badge } from "../primitives/Badge";
import { EmptyState } from "../primitives/EmptyState";
import { TouchButton } from "../primitives/TouchButton";
import { ClockGlyph, PlayGlyph, RefreshGlyph } from "./decompIcons";

// ── Types ──────────────────────────────────────────────────────────────────

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

export interface RunnerScheduleProps {
  data?: RunnerScheduleData;
  loading?: boolean;
  onRefresh?: () => void;
  onSave: (draft: ScheduleConfig, applyNow: boolean) => void;
}

export function RunnerSchedulePage(): React.ReactElement {
  const [data, setData] = useState<RunnerScheduleData>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    legacyFetch("/api/fleet/schedule", { signal })
      .then((r) => {
        if (!r.ok) throw new Error("schedule HTTP " + r.status);
        return r.json();
      })
      .then((payload: RunnerScheduleData | null) => {
        setData(payload || {});
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err.message : "Failed to load runner schedule",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const save = useCallback((draft: ScheduleConfig, applyNow: boolean) => {
    setLoading(true);
    setError(null);
    legacyFetch("/api/fleet/schedule", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ schedule: draft, apply: applyNow }),
    })
      .then((r) => {
        if (!r.ok) throw new Error("save failed");
        return r.json();
      })
      .then((payload: RunnerScheduleData | null) => {
        setData(payload || {});
      })
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Failed to save runner schedule",
        );
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <div>
      {error ? (
        <div
          className="section runner-schedule__error"
          role="alert"
        >
          Failed to load runner schedule: {error}
          <button
            className="btn runner-schedule__retry-button"
            type="button"
            onClick={() => refresh()}
          >
            Retry
          </button>
        </div>
      ) : null}
      <RunnerScheduleTab
        data={data}
        loading={loading}
        onRefresh={() => refresh()}
        onSave={save}
      />
    </div>
  );
}

export function RunnerScheduleTab({
  data,
  loading,
  onRefresh,
  onSave,
}: RunnerScheduleProps): React.ReactElement {
  const d = data || {};
  const schedule = d.schedule || {};
  const state = d.state || {};
  const [draft, setDraftRaw] = useState<ScheduleConfig>(schedule);
  const effectiveDraft = draft || schedule;
  const schedules = effectiveDraft.schedules || [];

  useEffect(() => {
    setDraftRaw(schedule);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.config_path, state.timestamp]);

  function updateSchedule(
    index: number,
    key: keyof RunnerScheduleEntry,
    value: string,
  ): void {
    const next: ScheduleConfig = Object.assign({}, effectiveDraft, {
      schedules: schedules.map((entry, i) => {
        if (i !== index) return entry;
        const copy: RunnerScheduleEntry = Object.assign({}, entry);
        if (key === "runners") {
          copy.runners = Number(value);
        } else {
          // start / end are the only other editable string fields.
          (copy as unknown as Record<string, unknown>)[key] = value;
        }
        return copy;
      }),
    });
    setDraftRaw(next);
  }

  return (
    <div className="runner-schedule">
      <div className="stat-row">
        <Stat
          label="Desired"
          value={state.desired != null ? state.desired : "-"}
          color="var(--accent-blue)"
          sub={state.reason || "schedule"}
        />
        <Stat
          label="Online"
          value={state.online != null ? state.online : "-"}
          color="var(--accent-green)"
          sub={"installed " + (state.installed != null ? state.installed : "-")}
        />
        <Stat
          label="Busy"
          value={state.busy != null ? state.busy : "-"}
          color="var(--accent-orange)"
          sub={"idle " + (state.idle != null ? state.idle : "-")}
        />
        <Stat
          label="Offline"
          value={state.offline != null ? state.offline : "-"}
          color="var(--accent-red)"
          sub={"max " + (d.max_runners || "-")}
        />
      </div>
      <div className="section-header">
        <span className="section-title">
          <ClockGlyph size={14} /> Runner Capacity
        </span>
        <div className="runner-schedule__actions">
          {loading ? (
            <Badge tone="neutral" size="sm">
              Saving...
            </Badge>
          ) : null}
          <TouchButton
            aria-label="Refresh runner capacity"
            className="runner-schedule__icon-button"
            onClick={onRefresh}
          >
            <RefreshGlyph size={12} />
          </TouchButton>
          <TouchButton
            className="runner-schedule__action-button"
            onClick={() => {
              onSave(effectiveDraft, false);
            }}
            disabled={loading || !effectiveDraft.schedules}
          >
            Save
          </TouchButton>
          <TouchButton
            className="runner-schedule__action-button"
            onClick={() => {
              onSave(effectiveDraft, true);
            }}
            disabled={loading || !effectiveDraft.schedules}
            variant="primary"
          >
            <PlayGlyph size={12} />
            Apply Now
          </TouchButton>
        </div>
      </div>
      <div className="card runner-schedule__card">
        <div className="runner-schedule__meta">
          <span>
            {(d.machine || "Local machine") +
              (d.aliases && d.aliases.length
                ? " aliases: " + d.aliases.join(", ")
                : "")}
          </span>
          <Badge tone={state.available ? "success" : "warning"} size="sm">
            {state.available ? "scheduler installed" : "scheduler missing"}
          </Badge>
        </div>
        {state.error ? (
          <EmptyState
            variant="error"
            title={state.error}
            description="Check the scheduler service and save a valid runner-capacity schedule before applying changes."
          />
        ) : null}
        {schedules.length ? (
          <div className="runner-schedule__table-wrap">
            <table className="table runner-schedule__table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Days</th>
                  <th>Start</th>
                  <th>End</th>
                  <th>Runners</th>
                </tr>
              </thead>
              <tbody>
                {schedules.map((entry, index) => (
                  <tr key={entry.name + index}>
                    <td>{entry.name}</td>
                    <td className="runner-schedule__days-cell">
                      {(entry.days || []).join(", ")}
                    </td>
                    <td>
                      <input
                        className="runner-schedule__time-input"
                        value={entry.start}
                        onInput={(e) => {
                          updateSchedule(
                            index,
                            "start",
                            (e.target as HTMLInputElement).value,
                          );
                        }}
                      />
                    </td>
                    <td>
                      <input
                        className="runner-schedule__time-input"
                        value={entry.end}
                        onInput={(e) => {
                          updateSchedule(
                            index,
                            "end",
                            (e.target as HTMLInputElement).value,
                          );
                        }}
                      />
                    </td>
                    <td>
                      <input
                        className="runner-schedule__runner-input"
                        type="number"
                        min={0}
                        max={d.max_runners || 99}
                        value={entry.runners}
                        onInput={(e) => {
                          updateSchedule(
                            index,
                            "runners",
                            (e.target as HTMLInputElement).value,
                          );
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="No runner capacity windows"
            description="Add schedule entries in the runner configuration before saving or applying capacity changes."
          />
        )}
        <div className="runner-schedule__footer">
          {"Config: " +
            (d.config_path || "-") +
            " | timers: scheduler " +
            ((d.timers && d.timers["runner-scheduler.timer"]) || "-") +
            ", cleanup " +
            ((d.timers && d.timers["runner-cleanup.timer"]) || "-")}
        </div>
      </div>
    </div>
  );
}

export default RunnerScheduleTab;
