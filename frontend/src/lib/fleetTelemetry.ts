/**
 * fleetTelemetry.ts — pure telemetry/formatting helpers used by the Fleet
 * overview tab, extracted (behaviour 1:1) from the legacy `App.tsx` monolith
 * as part of the decomposition epic (#836, pass 12).
 *
 * These were FleetTab-only inline helpers in the legacy file; lifting them to
 * `lib/` makes them unit-testable (legacy/** is excluded from coverage,
 * lib/** is measured) without changing any observable behaviour.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 1:1 port of dynamically-typed legacy runner/node telemetry payloads; the backend response shapes lack complete TypeScript definitions. */
import { parseRunnerName } from "./fleetMachines";

/** Relative "x ago" label for an ISO timestamp (matches legacy `timeAgo`). */
export function timeAgo(d: unknown): string {
  if (!d) return "";
  const s = (Date.now() - new Date(d as string).getTime()) / 1000;
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

/** Human-friendly duration from seconds (matches legacy `formatDuration`). */
export function formatDuration(s: number): string {
  if (!s || s < 0) return "-";
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + (s % 60) + "s";
}

/** Clamp a numeric percent into the integer range [0, 100]. */
export function boundedPercent(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, Math.round(n)));
}

/** Translucent fill colour for a CPU/RAM meter, keyed on the percent value. */
export function cpuColor(p: number): string {
  return p < 30
    ? "rgba(63,185,80,0.3)"
    : p < 60
      ? "rgba(63,185,80,0.6)"
      : p < 80
        ? "rgba(210,153,34,0.6)"
        : "rgba(248,81,73,0.7)";
}

export interface RunnerTelemetry {
  machine: string;
  node: any;
  cpu: number;
  memory: number;
  uptime: string;
  seen: string;
}

/**
 * Resolve the machine-level CPU/RAM/uptime telemetry for a single runner by
 * looking up its parsed machine in the nodes-by-name map.
 */
export function machineTelemetryForRunner(
  runner: any,
  nodesByName: Record<string, any>,
): RunnerTelemetry {
  const machine = parseRunnerName(runner.name).machine;
  const node = nodesByName[machine.toLowerCase()] || {};
  const sys = node.system || {};
  const cpu = sys.cpu || {};
  const mem = sys.memory || {};
  const cpuPct = boundedPercent(cpu.percent_1m_avg || cpu.percent || 0);
  const memPct = mem.total_gb
    ? boundedPercent((1 - mem.available_gb / mem.total_gb) * 100)
    : boundedPercent(mem.percent || 0);
  return {
    machine: machine,
    node: node,
    cpu: cpuPct,
    memory: memPct,
    uptime: sys.uptime_seconds ? formatDuration(sys.uptime_seconds) : "no uptime",
    seen: node.last_seen ? timeAgo(node.last_seen) : "not seen",
  };
}

/** Find the active (in-progress/queued) run a runner is currently executing. */
export function runnerCurrentRun(runner: any, runs: any[]): any {
  return (runs || []).find(function (run: any) {
    const status = String(run.status || "").toLowerCase();
    const isActive =
      status === "in_progress" ||
      status === "queued" ||
      status === "waiting" ||
      (!run.conclusion && status !== "completed");
    return (
      isActive &&
      (run.runner_name === runner.name || run.runner_id === runner.id)
    );
  });
}

/** One-line activity label for a runner's current run (or "idle"). */
export function compactRunnerActivity(currentRun: any): string {
  if (!currentRun) return "idle";
  if (currentRun.workflow_name) return currentRun.workflow_name;
  if (currentRun.name) return currentRun.name;
  if (currentRun.status) return currentRun.status;
  return "running";
}

/** Abbreviate a git SHA to 7 chars (matches legacy `shortSha`). */
export function shortSha(sha: unknown): string {
  return sha ? String(sha).slice(0, 7) : "unknown";
}
