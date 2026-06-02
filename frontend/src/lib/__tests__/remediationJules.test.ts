// @vitest-environment jsdom
/**
 * Unit tests for lib/remediationJules.ts — the Jules manual-dispatch helper
 * extracted from the legacy App.tsx (decomposition #836, pass 11).
 *
 * Covers the three terminal paths the legacy inline `.then`/`.catch` had:
 * success, HTTP-error (with backend `detail`), and network rejection. Each
 * flashes a banner via the supplied setter and auto-clears it after 6s.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dispatchJulesWorkflow } from "../remediationJules";

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("dispatchJulesWorkflow", () => {
  it("POSTs to the dispatch endpoint with the workflow file and defaults", async () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response),
    );
    vi.stubGlobal("fetch", fetchFn);
    const setMsg = vi.fn();

    await dispatchJulesWorkflow("jules.yml", setMsg);

    expect(fetchFn).toHaveBeenCalledWith(
      "/api/agent-remediation/dispatch-jules",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          workflow_file: "jules.yml",
          ref: "main",
          inputs: {},
        }),
      }),
    );
    expect(setMsg).toHaveBeenCalledWith({
      type: "success",
      text: "Dispatched jules.yml",
    });

    vi.advanceTimersByTime(6000);
    expect(setMsg).toHaveBeenLastCalledWith(null);
  });

  it("honours a custom ref and inputs", async () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response),
    );
    vi.stubGlobal("fetch", fetchFn);

    await dispatchJulesWorkflow("x.yml", vi.fn(), {
      ref: "dev",
      inputs: { foo: "bar" },
    });

    expect(fetchFn).toHaveBeenCalledWith(
      "/api/agent-remediation/dispatch-jules",
      expect.objectContaining({
        body: JSON.stringify({
          workflow_file: "x.yml",
          ref: "dev",
          inputs: { foo: "bar" },
        }),
      }),
    );
  });

  it("reports the backend detail on an HTTP error", async () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: "nope" }),
      } as Response),
    );
    vi.stubGlobal("fetch", fetchFn);
    const setMsg = vi.fn();

    await dispatchJulesWorkflow("jules.yml", setMsg);

    expect(setMsg).toHaveBeenCalledWith({
      type: "error",
      text: "Dispatch failed: nope",
    });
  });

  it("reports a network rejection", async () => {
    const fetchFn = vi.fn(() => Promise.reject(new Error("offline")));
    vi.stubGlobal("fetch", fetchFn);
    const setMsg = vi.fn();

    await dispatchJulesWorkflow("jules.yml", setMsg);

    expect(setMsg).toHaveBeenCalledWith({
      type: "error",
      text: expect.stringContaining("Dispatch error:"),
    });
  });
});
