/**
 * Tests for fleetEvents — client event model + alarm fold (issue #863).
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { FleetEvent } from "../fleetEvents";
import {
  activeConditions,
  eventKey,
  eventsToAlerts,
  filterBySeverity,
  loadEvents,
  mergeEvents,
  saveEvents,
} from "../fleetEvents";

function ev(partial: Partial<FleetEvent> & Pick<FleetEvent, "ts">): FleetEvent {
  return {
    severity: "info",
    kind: "runner_online",
    title: "t",
    detail: "",
    ...partial,
  };
}

beforeEach(() => localStorage.clear());
afterEach(() => localStorage.clear());

describe("mergeEvents", () => {
  it("de-duplicates by key and sorts newest-first", () => {
    const a = ev({ ts: 100, kind: "runner_offline", title: "A", node: "N1" });
    const b = ev({ ts: 200, kind: "runner_online", title: "B", node: "N1" });
    const merged = mergeEvents([a], [a, b]);
    expect(merged.map((e) => e.ts)).toEqual([200, 100]);
  });

  it("caps at the max retained count, dropping oldest", () => {
    const incoming = Array.from({ length: 10 }, (_, i) => ev({ ts: i }));
    const merged = mergeEvents([], incoming, 3);
    expect(merged.map((e) => e.ts)).toEqual([9, 8, 7]);
  });

  it("does not mutate inputs", () => {
    const existing = [ev({ ts: 1 })];
    const incoming = [ev({ ts: 2 })];
    mergeEvents(existing, incoming);
    expect(existing).toHaveLength(1);
    expect(incoming).toHaveLength(1);
  });
});

describe("eventKey", () => {
  it("differs for different occurrences but matches identical ones", () => {
    const a = ev({ ts: 5, kind: "low_disk", title: "x", node: "N" });
    const b = ev({ ts: 5, kind: "low_disk", title: "x", node: "N" });
    const c = ev({ ts: 6, kind: "low_disk", title: "x", node: "N" });
    expect(eventKey(a)).toBe(eventKey(b));
    expect(eventKey(a)).not.toBe(eventKey(c));
  });
});

describe("filterBySeverity", () => {
  const events = [
    ev({ ts: 1, severity: "info" }),
    ev({ ts: 2, severity: "warning" }),
    ev({ ts: 3, severity: "critical" }),
  ];
  it("returns all for null/all", () => {
    expect(filterBySeverity(events, "all")).toHaveLength(3);
    expect(filterBySeverity(events, null)).toHaveLength(3);
  });
  it("filters to a single severity", () => {
    expect(filterBySeverity(events, "critical").map((e) => e.ts)).toEqual([3]);
  });
});

describe("persistence", () => {
  it("round-trips through localStorage", () => {
    const events = [ev({ ts: 1, title: "persist me" })];
    saveEvents(events);
    expect(loadEvents()).toEqual(events);
  });
  it("returns [] when nothing stored", () => {
    expect(loadEvents()).toEqual([]);
  });
  it("ignores malformed stored data", () => {
    localStorage.setItem("fleetEvents:v1", "{not an array}");
    expect(loadEvents()).toEqual([]);
  });
});

describe("activeConditions", () => {
  it("a later online cancels an earlier offline for the same node", () => {
    const events = [
      ev({ ts: 100, kind: "runner_offline", title: "N1 offline — timeout", node: "N1" }),
      ev({ ts: 200, kind: "runner_online", title: "N1 back online", node: "N1" }),
    ];
    const c = activeConditions(events);
    expect(c.offlineNodes).toEqual([]);
  });

  it("classifies a disk-pressure offline node", () => {
    const events = [
      ev({
        ts: 100,
        kind: "runner_offline",
        severity: "critical",
        title: "N1 offline — disk pressure",
        detail: "4.0 GB free",
        node: "N1",
      }),
    ];
    const c = activeConditions(events);
    expect(c.offlineNodes).toContain("N1");
    expect(c.diskOfflineNodes).toContain("N1");
  });

  it("tracks low-disk while online but not when offline", () => {
    const online = activeConditions([
      ev({ ts: 100, kind: "low_disk", severity: "warning", title: "N1 low disk", node: "N1" }),
    ]);
    expect(online.lowDiskNodes).toContain("N1");

    const offlineToo = activeConditions([
      ev({ ts: 50, kind: "low_disk", title: "N1 low disk", node: "N1" }),
      ev({ ts: 100, kind: "runner_offline", title: "N1 offline — disk pressure", node: "N1" }),
    ]);
    expect(offlineToo.lowDiskNodes).not.toContain("N1");
  });
});

describe("eventsToAlerts", () => {
  it("produces a critical disk-pressure alert for disk-offline nodes", () => {
    const alerts = eventsToAlerts([
      ev({ ts: 1, kind: "runner_offline", title: "N1 offline — disk pressure", node: "N1" }),
    ]);
    const disk = alerts.find((a) => a.id === "disk-pressure");
    expect(disk).toBeDefined();
    expect(disk?.level).toBe("critical");
    expect(disk?.title).toMatch(/disk pressure/i);
    expect(disk?.contentHash).toBeTruthy();
  });

  it("produces a warning disk alert for low-disk-only", () => {
    const alerts = eventsToAlerts([
      ev({ ts: 1, kind: "low_disk", severity: "warning", title: "N1 low disk", node: "N1" }),
    ]);
    const disk = alerts.find((a) => a.id === "disk-pressure");
    expect(disk?.level).toBe("warning");
  });

  it("produces a separate runners-offline alert for non-disk offline", () => {
    const alerts = eventsToAlerts([
      ev({ ts: 1, kind: "runner_offline", title: "N2 offline — timeout", node: "N2" }),
    ]);
    expect(alerts.find((a) => a.id === "runners-offline")).toBeDefined();
    expect(alerts.find((a) => a.id === "disk-pressure")).toBeUndefined();
  });

  it("emits nothing when all healthy", () => {
    expect(eventsToAlerts([ev({ ts: 1, kind: "runner_online", title: "ok", node: "N1" })])).toEqual([]);
  });
});
