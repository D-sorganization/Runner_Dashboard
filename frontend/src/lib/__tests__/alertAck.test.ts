/**
 * Unit tests for alertAck — durable acknowledge/snooze layer (issue #819).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ack,
  ackedCount,
  clear,
  isAcked,
  snooze,
  SNOOZE_DURATIONS_MS,
} from "../alertAck";

const ID = "machines-offline";
const HASH = "abc123";

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("alertAck — baseline", () => {
  it("reports not-acked for an unknown alert", () => {
    expect(isAcked(ID, HASH)).toBe(false);
  });

  it("throws when id is empty", () => {
    expect(() => isAcked("", HASH)).toThrow();
    expect(() => ack("", HASH)).toThrow();
    expect(() => snooze("", HASH, 1000)).toThrow();
    expect(() => clear("")).toThrow();
  });
});

describe("alertAck — permanent ack", () => {
  it("suppresses an alert after ack with the same hash", () => {
    ack(ID, HASH);
    expect(isAcked(ID, HASH)).toBe(true);
  });

  it("re-surfaces when the contentHash changes", () => {
    ack(ID, HASH);
    expect(isAcked(ID, "different-hash")).toBe(false);
  });

  it("persists indefinitely across time (no snooze expiry)", () => {
    ack(ID, HASH, 1_000);
    const farFuture = 1_000 + 365 * 24 * 60 * 60 * 1000;
    expect(isAcked(ID, HASH, farFuture)).toBe(true);
  });

  it("requires a non-empty hash", () => {
    expect(() => ack(ID, "")).toThrow();
  });
});

describe("alertAck — snooze", () => {
  it("suppresses within the snooze window", () => {
    snooze(ID, HASH, 10_000, 1_000);
    expect(isAcked(ID, HASH, 5_000)).toBe(true);
  });

  it("re-surfaces once the snooze window elapses", () => {
    snooze(ID, HASH, 10_000, 1_000);
    expect(isAcked(ID, HASH, 11_000)).toBe(false);
  });

  it("re-surfaces at exactly the boundary (>= is the contract)", () => {
    snooze(ID, HASH, 10_000, 1_000);
    expect(isAcked(ID, HASH, 11_000)).toBe(false);
    expect(isAcked(ID, HASH, 10_999)).toBe(true);
  });

  it("re-surfaces before expiry if content changes", () => {
    snooze(ID, HASH, 10_000, 1_000);
    expect(isAcked(ID, "new-hash", 5_000)).toBe(false);
  });

  it("rejects a non-positive duration", () => {
    expect(() => snooze(ID, HASH, 0)).toThrow();
    expect(() => snooze(ID, HASH, -5)).toThrow();
  });

  it("exposes sane preset durations", () => {
    expect(SNOOZE_DURATIONS_MS.oneHour).toBe(3_600_000);
    expect(SNOOZE_DURATIONS_MS.fourHours).toBe(14_400_000);
    expect(SNOOZE_DURATIONS_MS.oneDay).toBe(86_400_000);
  });
});

describe("alertAck — clear", () => {
  it("un-acknowledges a previously acked alert", () => {
    ack(ID, HASH);
    expect(isAcked(ID, HASH)).toBe(true);
    clear(ID);
    expect(isAcked(ID, HASH)).toBe(false);
  });

  it("is a no-op for an unknown id", () => {
    expect(() => clear("never-acked")).not.toThrow();
  });
});

describe("alertAck — persistence across module reads", () => {
  it("survives independent isAcked calls (durable in localStorage)", () => {
    ack(ID, HASH, 1_000);
    // Simulate a fresh poll: same data still suppressed.
    expect(isAcked(ID, HASH, 2_000)).toBe(true);
    expect(isAcked(ID, HASH, 3_000)).toBe(true);
  });
});

describe("alertAck — ackedCount", () => {
  it("counts only currently-valid suppressions for the live alert set", () => {
    const alerts = [
      { id: "machines-offline", contentHash: "h1" },
      { id: "wsl-keepalive", contentHash: "h2" },
      { id: "success-rate", contentHash: "h3" },
    ];
    ack("machines-offline", "h1", 1_000);
    snooze("wsl-keepalive", "h2", 10_000, 1_000);
    // success-rate not acked.
    expect(ackedCount(alerts, 5_000)).toBe(2);
    // After the snooze elapses, only the permanent ack counts.
    expect(ackedCount(alerts, 20_000)).toBe(1);
  });

  it("does not count an ack whose content has since changed", () => {
    ack("machines-offline", "old-hash");
    const alerts = [{ id: "machines-offline", contentHash: "new-hash" }];
    expect(ackedCount(alerts)).toBe(0);
  });
});

describe("alertAck — storage resilience", () => {
  it("degrades to in-memory when localStorage.setItem throws", () => {
    const spy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("quota exceeded");
      });
    // Should not throw to the caller.
    expect(() => ack(ID, HASH, 1_000)).not.toThrow();
    spy.mockRestore();
    // After restore the in-memory fallback still reports the ack.
    expect(isAcked(ID, HASH, 2_000)).toBe(true);
  });
});
