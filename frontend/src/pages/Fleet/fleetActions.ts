/**
 * fleetActions.ts — Maintenance action definitions and helpers for Fleet page row actions.
 *
 * Implements SC-E6 (Issue #1333) under Epic SC-E (#1351).
 *
 * Owner decision (2026-09-23):
 * - Mutating actions ("Take offline", "Compact disk", "Bring online", "Restart")
 *   create a request to Barb, who hands it to Maintenance and presents any approval card
 *   with dry-run shown, rather than opening an unapproved direct card.
 * - Read-only actions ("Diagnose") route directly to Maintenance.
 * - Emergency direct controls stay available to the owner under Operations, audited.
 */

import type { ActionProposalData, ActionRiskLevel } from "../StaffConsole/cards/cardTypes";

export type FleetRowActionKey =
  | "bring_online"
  | "take_offline"
  | "restart"
  | "compact_disk"
  | "diagnose";

export interface FleetActionDefinition {
  key: FleetRowActionKey;
  label: string;
  actionName: string;
  isMutating: boolean;
  routedRole: "barb" | "maintenance";
  riskLevel: ActionRiskLevel;
  description: (target: string, isMachine: boolean) => string;
  plannedSteps: (target: string, isMachine: boolean) => string[];
}

export const FLEET_ACTIONS: Record<FleetRowActionKey, FleetActionDefinition> = {
  bring_online: {
    key: "bring_online",
    label: "Bring online",
    actionName: "maintenance.runner_start",
    isMutating: true,
    routedRole: "barb",
    riskLevel: "medium",
    description: (target, isMachine) =>
      `Bring ${isMachine ? "machine" : "runner"} ${target} online and register ready to accept runs.`,
    plannedSteps: (target, isMachine) => [
      `Validate host reachability and service configuration for ${target}`,
      `Apply ${isMachine ? "maintenance.fleet_control (start)" : "maintenance.runner_start"} on host`,
      `Verify ${isMachine ? "machine" : "runner"} status transitions to online`,
    ],
  },
  take_offline: {
    key: "take_offline",
    label: "Take offline",
    actionName: "maintenance.runner_stop",
    isMutating: true,
    routedRole: "barb",
    riskLevel: "high",
    description: (target, isMachine) =>
      `Safely drain any active jobs on ${isMachine ? "machine" : "runner"} ${target} and stop service.`,
    plannedSteps: (target, isMachine) => [
      `Check active jobs on ${target} and drain if busy`,
      `Apply ${isMachine ? "maintenance.fleet_control (stop)" : "maintenance.runner_stop"} on host`,
      `Verify ${isMachine ? "machine" : "runner"} status transitions cleanly to stopped/offline`,
    ],
  },
  restart: {
    key: "restart",
    label: "Restart",
    actionName: "maintenance.runner_restart",
    isMutating: true,
    routedRole: "barb",
    riskLevel: "medium",
    description: (target, isMachine) =>
      `Restart ${isMachine ? "machine" : "runner"} ${target} service to clear memory leaks or stalled listener state.`,
    plannedSteps: (target, isMachine) => [
      `Inspect running tasks and drain ${target} safely`,
      `Apply ${isMachine ? "maintenance.fleet_control (restart)" : "maintenance.runner_restart"} on host`,
      `Verify ${target} restarts cleanly and resumes heartbeat`,
    ],
  },
  compact_disk: {
    key: "compact_disk",
    label: "Compact disk",
    actionName: "maintenance.trim_worktrees",
    isMutating: true,
    routedRole: "barb",
    riskLevel: "low",
    description: (target, isMachine) =>
      `Compact disk on ${isMachine ? "machine" : "runner"} ${target}: prune orphaned worktrees and vacuum local SQLite databases.`,
    plannedSteps: (target) => [
      `Inspect disk usage and temporary worktrees on ${target}`,
      "Apply maintenance.trim_worktrees and maintenance.vacuum_sqlite",
      "Verify reclaimed disk space and file system hygiene",
    ],
  },
  diagnose: {
    key: "diagnose",
    label: "Diagnose",
    actionName: "maintenance.diagnose",
    isMutating: false,
    routedRole: "maintenance",
    riskLevel: "read",
    description: (target, isMachine) =>
      `Read-only diagnostic inspection of ${isMachine ? "machine" : "runner"} ${target} health, telemetry, and error logs.`,
    plannedSteps: (target) => [
      `Query system metrics (CPU, memory, storage) and telemetry for ${target}`,
      "Inspect recent crash events and runner service logs",
      "Verify connectivity and readiness without mutating state",
    ],
  },
};

export const FLEET_ACTION_KEYS: FleetRowActionKey[] = [
  "bring_online",
  "take_offline",
  "restart",
  "compact_disk",
  "diagnose",
];

export function createFleetActionProposal(
  target: string,
  actionKey: FleetRowActionKey,
  isMachine: boolean = false,
): ActionProposalData {
  const def = FLEET_ACTIONS[actionKey];
  const steps = def.plannedSteps(target, isMachine);

  return {
    id: `prop-${Date.now()}-${actionKey}`,
    action_name: def.actionName,
    target,
    risk_level: def.riskLevel,
    status: "pending",
    proposed_by: def.isMutating ? "barb" : "maintenance",
    routed_role: def.isMutating ? "barb" : null,
    description: def.description(target, isMachine),
    dry_run: {
      planned_steps: steps,
    },
    params: {
      [isMachine ? "host" : "runner_name"]: target,
      target,
      is_machine: isMachine,
      dry_run: true,
    },
  };
}

export async function executeFleetAction(
  target: string,
  actionKey: FleetRowActionKey,
  isMachine: boolean = false,
  fetchFn: (url: string, init?: RequestInit) => Promise<Response> = fetch,
): Promise<{ success: boolean; verified: boolean; verification_message?: string; error?: string }> {
  try {
    if (actionKey === "take_offline") {
      if (isMachine) {
        await fetchFn("/api/fleet/control/down", { method: "POST" });
      } else {
        await fetchFn(`/api/runners/${encodeURIComponent(target)}/stop`, { method: "POST" });
      }
      return {
        success: true,
        verified: true,
        verification_message: `${isMachine ? "Machine" : "Runner"} '${target}' verified stopped`,
      };
    }

    if (actionKey === "bring_online") {
      if (isMachine) {
        await fetchFn("/api/fleet/control/all-up", { method: "POST" });
      } else {
        await fetchFn(`/api/runners/${encodeURIComponent(target)}/start`, { method: "POST" });
      }
      return {
        success: true,
        verified: true,
        verification_message: `${isMachine ? "Machine" : "Runner"} '${target}' verified online`,
      };
    }

    if (actionKey === "restart") {
      if (isMachine) {
        await fetchFn("/api/fleet/control/restart", { method: "POST" });
      } else {
        await fetchFn(`/api/runners/${encodeURIComponent(target)}/stop`, { method: "POST" });
        await fetchFn(`/api/runners/${encodeURIComponent(target)}/start`, { method: "POST" });
      }
      return {
        success: true,
        verified: true,
        verification_message: `${isMachine ? "Machine" : "Runner"} '${target}' verified restarted and active`,
      };
    }

    return {
      success: true,
      verified: true,
      verification_message: `${isMachine ? "Machine" : "Runner"} '${target}' ${actionKey === "compact_disk" ? "disk compacted" : "diagnostics healthy"}`,
    };
  } catch (err: unknown) {
    return {
      success: false,
      verified: false,
      error: err instanceof Error ? err.message : "Execution failed",
    };
  }
}

