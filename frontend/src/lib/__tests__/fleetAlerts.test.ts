/**
 * Unit tests for computeFleetAlerts — the hero-panel rollup logic
 * extracted from legacy/App.tsx so it can be tested without the
 * `h()` tree.
 */

import { describe, expect, it } from "vitest";
import {
  computeFleetAlerts,
  fleetLevelLabel,
  type FleetState,
} from "../fleetAlerts";

const baseState: FleetState = {
  machineCount: 4,
  machineOnline: 4,
  machineNodes: [
    { name: "ControlTower", online: true },
    { name: "DeskComputer", online: true },
    { name: "OGLaptop", online: true },
    { name: "Brick", online: true },
  ],
  watchdog: { status: "healthy" },
  stats: { success_rate: 95, runs_success: 95 },
  completedRuns: 100,
  runnerAudit: { violations: [] },
};

describe("computeFleetAlerts — happy path", () => {
  it("returns level=ok with no alerts when everything is healthy", () => {
    const result = computeFleetAlerts(baseState);
    expect(result.level).toBe("ok");
    expect(result.alerts).toEqual([]);
  });

  it("treats a registry with zero machines as ok (no false alert)", () => {
    const result = computeFleetAlerts({
      ...baseState,
      machineCount: 0,
      machineOnline: 0,
      machineNodes: [],
    });
    expect(result.level).toBe("ok");
    expect(result.alerts).toEqual([]);
  });
});

describe("computeFleetAlerts — Rule 1: machines offline", () => {
  it("flags critical when one machine is offline", () => {
    const result = computeFleetAlerts({
      ...baseState,
      machineOnline: 3,
      machineNodes: [
        { name: "ControlTower", online: true },
        { name: "DeskComputer", online: true },
        { name: "OGLaptop", online: true },
        { name: "Brick", online: false },
      ],
    });
    expect(result.level).toBe("critical");
    expect(result.alerts).toHaveLength(1);
    expect(result.alerts[0]).toMatchObject({
      level: "critical",
      title: "1 machine(s) offline",
      detail: "Brick",
    });
  });

  it("lists multiple offline machine names in the detail", () => {
    const result = computeFleetAlerts({
      ...baseState,
      machineOnline: 2,
      machineNodes: [
        { name: "ControlTower", online: true },
        { name: "DeskComputer", online: true },
        { name: "OGLaptop", online: false },
        { name: "Brick", online: false },
      ],
    });
    expect(result.alerts[0].title).toBe("2 machine(s) offline");
    expect(result.alerts[0].detail).toBe("OGLaptop, Brick");
  });

  it("falls back to a generic detail when no machine names are available", () => {
    const result = computeFleetAlerts({
      ...baseState,
      machineOnline: 3,
      machineNodes: [],
    });
    expect(result.alerts[0].detail).toBe("see Machine Health below");
  });
});

describe("computeFleetAlerts — Rule 2: WSL keepalive", () => {
  it("flags warning when watchdog is degraded", () => {
    const result = computeFleetAlerts({
      ...baseState,
      watchdog: { status: "degraded", summary: "scheduled task misconfigured" },
    });
    expect(result.level).toBe("warning");
    expect(result.alerts).toHaveLength(1);
    expect(result.alerts[0]).toMatchObject({
      level: "warning",
      title: "WSL Keepalive: degraded",
      detail: "scheduled task misconfigured",
    });
  });

  it("flags CRITICAL (not warning) when watchdog is legacy", () => {
    const result = computeFleetAlerts({
      ...baseState,
      watchdog: { status: "legacy" },
    });
    expect(result.level).toBe("critical");
    expect(result.alerts[0].level).toBe("critical");
    expect(result.alerts[0].title).toBe("WSL Keepalive: legacy");
  });

  it("uses detail when summary is absent", () => {
    const result = computeFleetAlerts({
      ...baseState,
      watchdog: { status: "degraded", detail: "fallback detail string" },
    });
    expect(result.alerts[0].detail).toBe("fallback detail string");
  });

  it("falls back to a generic message when neither summary nor detail is set", () => {
    const result = computeFleetAlerts({
      ...baseState,
      watchdog: { status: "degraded" },
    });
    expect(result.alerts[0].detail).toBe("WSL keepalive needs attention");
  });

  it("does not flag when watchdog.status is healthy", () => {
    const result = computeFleetAlerts(baseState);
    expect(result.alerts.find((a) => a.title.startsWith("WSL Keepalive"))).toBeUndefined();
  });
});

describe("computeFleetAlerts — Rule 3: success rate", () => {
  it("flags warning when success_rate is between 40 and 70", () => {
    const result = computeFleetAlerts({
      ...baseState,
      stats: { success_rate: 55, runs_success: 55 },
    });
    expect(result.level).toBe("warning");
    expect(result.alerts[0]).toMatchObject({
      level: "warning",
      title: "Success rate: 55%",
    });
  });

  it("flags critical when success_rate is below 40", () => {
    const result = computeFleetAlerts({
      ...baseState,
      stats: { success_rate: 30, runs_success: 30 },
    });
    expect(result.level).toBe("critical");
    expect(result.alerts[0].level).toBe("critical");
  });

  it("does NOT flag when completedRuns is zero (no signal yet)", () => {
    const result = computeFleetAlerts({
      ...baseState,
      stats: { success_rate: 0 },
      completedRuns: 0,
    });
    expect(result.alerts.find((a) => a.title.startsWith("Success rate"))).toBeUndefined();
  });

  it("does NOT flag when success_rate is exactly 70 (threshold is strict)", () => {
    const result = computeFleetAlerts({
      ...baseState,
      stats: { success_rate: 70, runs_success: 70 },
    });
    expect(result.alerts.find((a) => a.title.startsWith("Success rate"))).toBeUndefined();
  });
});

describe("computeFleetAlerts — Rule 4: hosted-runner billing", () => {
  it("flags warning when there are violations", () => {
    const result = computeFleetAlerts({
      ...baseState,
      runnerAudit: { violations: [{}, {}, {}] },
    });
    expect(result.level).toBe("warning");
    expect(result.alerts[0]).toMatchObject({
      level: "warning",
      title: "3 job(s) on GitHub-hosted runners",
      detail: "Billing alert — see Runner Audit tab",
    });
  });

  it("does NOT flag when violations is an empty array", () => {
    const result = computeFleetAlerts(baseState);
    expect(result.alerts.find((a) => a.title.includes("GitHub-hosted"))).toBeUndefined();
  });
});

describe("computeFleetAlerts — severity rollup", () => {
  it("critical dominates warning when both are present", () => {
    const result = computeFleetAlerts({
      ...baseState,
      machineOnline: 3, // critical
      machineNodes: [
        { name: "ControlTower", online: true },
        { name: "DeskComputer", online: true },
        { name: "OGLaptop", online: true },
        { name: "Brick", online: false },
      ],
      runnerAudit: { violations: [{}] }, // warning
    });
    expect(result.level).toBe("critical");
    expect(result.alerts).toHaveLength(2);
  });

  it("emits alerts in stable rule order (machines → watchdog → success → hosted)", () => {
    const result = computeFleetAlerts({
      ...baseState,
      machineOnline: 3,
      machineNodes: [
        { name: "A", online: true },
        { name: "A", online: true },
        { name: "A", online: true },
        { name: "B", online: false },
      ],
      watchdog: { status: "degraded" },
      stats: { success_rate: 50, runs_success: 50 },
      runnerAudit: { violations: [{}] },
    });
    expect(result.alerts.map((a) => a.title)).toEqual([
      "1 machine(s) offline",
      "WSL Keepalive: degraded",
      "Success rate: 50%",
      "1 job(s) on GitHub-hosted runners",
    ]);
  });

  it("is a pure function — same input always yields same output", () => {
    const a = computeFleetAlerts(baseState);
    const b = computeFleetAlerts(baseState);
    expect(a).toEqual(b);
  });
});

describe("fleetLevelLabel", () => {
  it("maps ok → Operational", () => {
    expect(fleetLevelLabel("ok")).toBe("Operational");
  });

  it("maps warning → Degraded", () => {
    expect(fleetLevelLabel("warning")).toBe("Degraded");
  });

  it("maps critical → Critical", () => {
    expect(fleetLevelLabel("critical")).toBe("Critical");
  });
});
