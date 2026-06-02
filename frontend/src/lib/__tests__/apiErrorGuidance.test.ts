/**
 * Tests for apiErrorGuidance — map API failures to operator actions (#837).
 *
 * TDD: authored alongside the mapping module. Asserts each failure class
 * produces a concrete operator action and NEVER a raw status code.
 */
import { describe, it, expect } from "vitest";
import {
  guidanceForFailure,
  guidanceForResponse,
} from "../apiErrorGuidance";

describe("guidanceForFailure", () => {
  it("maps a fetch TypeError (daemon down) to a Local Tools action", () => {
    const g = guidanceForFailure({ error: new TypeError("Failed to fetch") });
    expect(g.kind).toBe("connection");
    expect(g.action).toMatch(/Local Tools/i);
    expect(g.action).toMatch(/Maxwell-Daemon/i);
  });

  it("maps a relayed httpx ConnectError string to a connection action", () => {
    const g = guidanceForFailure({ message: "ConnectError: connection refused" });
    expect(g.kind).toBe("connection");
    expect(g.action).toMatch(/start it from the Local Tools/i);
  });

  it("maps 401 to a Credentials token-refresh action", () => {
    const g = guidanceForFailure({ status: 401 });
    expect(g.kind).toBe("auth");
    expect(g.title).toMatch(/expired/i);
    expect(g.action).toMatch(/Credentials/i);
  });

  it("maps 403 to a permissions action", () => {
    const g = guidanceForFailure({ status: 403 });
    expect(g.kind).toBe("forbidden");
    expect(g.action).toMatch(/permission/i);
  });

  it("maps 404 to a deploy-drift / Diagnostics hint", () => {
    const g = guidanceForFailure({ status: 404 });
    expect(g.kind).toBe("not-found");
    expect(g.action).toMatch(/Diagnostics/i);
  });

  it("maps 5xx to a backend-error retry action", () => {
    const g = guidanceForFailure({ status: 503 });
    expect(g.kind).toBe("server");
    expect(g.action).toMatch(/retry/i);
  });

  it("falls back to a generic retry message for unknown failures", () => {
    const g = guidanceForFailure({ status: 418 });
    expect(g.kind).toBe("unknown");
    expect(g.title).toBeTruthy();
    expect(g.action).toBeTruthy();
  });

  it("never surfaces a bare status code as the action text", () => {
    for (const status of [401, 403, 404, 500, 502]) {
      const g = guidanceForFailure({ status });
      expect(g.action).not.toMatch(new RegExp(`\\b${status}\\b`));
      expect(g.action).not.toMatch(/HTTP \d/);
    }
  });
});

describe("guidanceForResponse", () => {
  it("derives guidance from a non-OK Response-like object", () => {
    const g = guidanceForResponse({ ok: false, status: 401 });
    expect(g.kind).toBe("auth");
  });
});
