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
import React, { useEffect, useState } from "react";
import { Stat } from "../components/Stat";
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

  function updateSchedule(index: number, key: keyof RunnerScheduleEntry, value: string): void {
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
    <div>
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
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {loading ? <span style={{ fontSize: 11, color: "var(--fg-muted)" }}>Saving...</span> : null}
          <button className="btn" onClick={onRefresh}>
            <RefreshGlyph size={12} />
          </button>
          <button
            className="btn"
            onClick={() => {
              onSave(effectiveDraft, false);
            }}
            disabled={loading || !effectiveDraft.schedules}
          >
            Save
          </button>
          <button
            className="btn"
            onClick={() => {
              onSave(effectiveDraft, true);
            }}
            disabled={loading || !effectiveDraft.schedules}
          >
            <PlayGlyph size={12} />
            Apply Now
          </button>
        </div>
      </div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 13, color: "var(--fg-muted)", marginBottom: 12 }}>
          {(d.machine || "Local machine") +
            (d.aliases && d.aliases.length ? " aliases: " + d.aliases.join(", ") : "") +
            " | scheduler " +
            (state.available ? "installed" : "missing")}
        </div>
        {state.error ? (
          <div style={{ color: "var(--accent-orange)", fontSize: 12, marginBottom: 12 }}>{state.error}</div>
        ) : null}
        <table className="table">
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
                <td style={{ fontSize: 12 }}>{(entry.days || []).join(", ")}</td>
                <td>
                  <input
                    value={entry.start}
                    onInput={(e) => {
                      updateSchedule(index, "start", (e.target as HTMLInputElement).value);
                    }}
                    style={{ width: 76 }}
                  />
                </td>
                <td>
                  <input
                    value={entry.end}
                    onInput={(e) => {
                      updateSchedule(index, "end", (e.target as HTMLInputElement).value);
                    }}
                    style={{ width: 76 }}
                  />
                </td>
                <td>
                  <input
                    type="number"
                    min={0}
                    max={d.max_runners || 99}
                    value={entry.runners}
                    onInput={(e) => {
                      updateSchedule(index, "runners", (e.target as HTMLInputElement).value);
                    }}
                    style={{ width: 64 }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 12 }}>
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
