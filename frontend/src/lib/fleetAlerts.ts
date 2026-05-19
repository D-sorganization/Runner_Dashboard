/**
 * Fleet-alert rollup logic for the Overview hero panel.
 *
 * Extracted from `frontend/src/legacy/App.tsx` so it can be unit-tested
 * without spinning up the whole legacy `h()`-tree. Pure function — no
 * React imports, no `h()`, no DOM. Consumed by the legacy FleetTab today;
 * the new shell migration can consume the same function unchanged.
 *
 * Severity contract:
 *   - "ok"       → no alerts; UI shows the green dot + "All systems nominal"
 *   - "warning"  → at least one warning-level alert; UI shows yellow dot
 *   - "critical" → at least one critical-level alert; UI shows red dot
 *
 * Rule ordering does NOT affect severity — the overall level is the max
 * severity across rules. Rule order DOES affect display order in the
 * alerts list (visually: most-actionable first).
 */

export type AlertLevel = "warning" | "critical";
export type FleetLevel = "ok" | AlertLevel;

export interface FleetAlert {
  level: AlertLevel;
  title: string;
  detail: string;
}

export interface FleetState {
  machineCount: number;
  machineOnline: number;
  /** Machine objects from /api/fleet/nodes. Only `name` and `online` are read. */
  machineNodes?: ReadonlyArray<{ name?: string; online?: boolean }>;
  /** Watchdog state from /api/watchdog. `status` is the load-bearing field. */
  watchdog?: { status?: string; summary?: string; detail?: string };
  /** Run statistics. success_rate is a percentage (0-100). */
  stats?: { success_rate?: number; runs_success?: number };
  /** Total completed runs in the recent window. Used as a denominator. */
  completedRuns?: number;
  /** Runner audit violations (GitHub-hosted runners). */
  runnerAudit?: { violations?: ReadonlyArray<unknown> };
}

export interface FleetAlertsResult {
  level: FleetLevel;
  alerts: FleetAlert[];
}

/**
 * Roll up cross-cutting fleet signals into a single status + list of
 * alerts. Pure function: same input → same output.
 *
 * Thresholds chosen to match operator perception:
 *   - any machine offline → critical (a host being down blocks job pickup)
 *   - watchdog "legacy"   → critical (security regression — old VBS path)
 *   - watchdog "degraded" → warning  (configured but not running)
 *   - success_rate < 40   → critical
 *   - success_rate < 70   → warning
 *   - hosted-runner violations → warning (billing impact, not outage)
 */
export function computeFleetAlerts(state: FleetState): FleetAlertsResult {
  const alerts: FleetAlert[] = [];

  // Rule 1: machine offline count
  const machineCount = state.machineCount ?? 0;
  const machineOnline = state.machineOnline ?? 0;
  if (machineCount > 0 && machineOnline < machineCount) {
    const offlineNames = (state.machineNodes ?? [])
      .filter((n) => n && !n.online)
      .map((n) => n.name ?? "")
      .filter(Boolean);
    alerts.push({
      level: "critical",
      title: `${machineCount - machineOnline} machine(s) offline`,
      detail: offlineNames.join(", ") || "see Machine Health below",
    });
  }

  // Rule 2: WSL keepalive watchdog
  const watchdogStatus = state.watchdog?.status;
  if (watchdogStatus && watchdogStatus !== "healthy") {
    alerts.push({
      level: watchdogStatus === "legacy" ? "critical" : "warning",
      title: `WSL Keepalive: ${watchdogStatus}`,
      detail:
        state.watchdog?.summary ||
        state.watchdog?.detail ||
        "WSL keepalive needs attention",
    });
  }

  // Rule 3: success rate
  const successRate = state.stats?.success_rate;
  const completedRuns = state.completedRuns ?? 0;
  if (
    typeof successRate === "number" &&
    successRate < 70 &&
    completedRuns > 0
  ) {
    alerts.push({
      level: successRate < 40 ? "critical" : "warning",
      title: `Success rate: ${successRate}%`,
      detail: `${state.stats?.runs_success ?? 0}/${completedRuns} recent runs passed`,
    });
  }

  // Rule 4: hosted-runner billing violations
  const violations = state.runnerAudit?.violations ?? [];
  if (violations.length > 0) {
    alerts.push({
      level: "warning",
      title: `${violations.length} job(s) on GitHub-hosted runners`,
      detail: "Billing alert — see Runner Audit tab",
    });
  }

  // Severity rollup: critical dominates warning
  let level: FleetLevel = "ok";
  if (alerts.some((a) => a.level === "critical")) {
    level = "critical";
  } else if (alerts.length > 0) {
    level = "warning";
  }

  return { level, alerts };
}

/**
 * Human-readable label for the FleetLevel. Kept separate from
 * computeFleetAlerts so the function stays pure and the label can be
 * localized independently in the future.
 */
export function fleetLevelLabel(level: FleetLevel): string {
  switch (level) {
    case "ok":
      return "Operational";
    case "warning":
      return "Degraded";
    case "critical":
      return "Critical";
  }
}
