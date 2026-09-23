// @vitest-environment jsdom
/**
 * Pure helpers behind the Fleet Command tab (#1233) and the Staff tab `?run=`
 * deep link it relies on.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../../lib/api";
import { describeError, expiresIn, findConflicts, toWorkRows, trackingUrl } from "../FleetCommand/fleetApi";
import { StaffPage } from "../Staff";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState({}, "", "/");
});

describe("fleetApi helpers", () => {
  it("resolves board tracking references to GitHub URLs", () => {
    expect(trackingUrl("#12", "Tools")).toBe("https://github.com/D-sorganization/Tools/issues/12");
    expect(trackingUrl("Tools#12")).toBe("https://github.com/D-sorganization/Tools/issues/12");
    expect(trackingUrl("other/Repo#3")).toBe("https://github.com/other/Repo/issues/3");
    expect(trackingUrl("https://github.com/x/y/pull/9")).toBe("https://github.com/x/y/pull/9");
    expect(trackingUrl("#12")).toBeNull(); // no project to anchor it
    expect(trackingUrl("javascript:alert(1)")).toBeNull();
    expect(trackingUrl("TBD", "Tools")).toBeNull();
  });

  it("formats expiry relative to now", () => {
    const now = Date.parse("2026-09-23T00:00:00Z");
    expect(expiresIn("2026-09-23T01:20:00Z", now)).toBe("in 1h 20m");
    expect(expiresIn("2026-09-23T00:05:00Z", now)).toBe("in 5m");
    expect(expiresIn("2026-09-25T03:00:00Z", now)).toBe("in 2d 3h");
    expect(expiresIn("2026-09-22T23:00:00Z", now)).toBe("expired");
    expect(expiresIn("", now)).toBe("");
    expect(expiresIn("garbage", now)).toBe("");
  });

  it("flags overlapping paths within one repo only", () => {
    const rows = toWorkRows(
      [
        { session: "a", agent: "codex", repo: "Tools", paths: ["src/pkg"] },
        { session: "b", agent: "gemini", repo: "tools", paths: ["src/pkg/mod.py"] },
        { session: "c", agent: "grok", repo: "Other", paths: ["src/pkg"] },
      ],
      [],
    );
    const conflicts = findConflicts(rows);
    expect(conflicts.get("board:a")).toEqual(["paths overlap gemini: src/pkg ↔ src/pkg/mod.py"]);
    expect(conflicts.get("board:b")).toEqual(["paths overlap codex: src/pkg/mod.py ↔ src/pkg"]);
    expect(conflicts.has("board:c")).toBe(false);
  });

  it("flattens structured error details instead of rendering objects", () => {
    expect(describeError(new ApiClientError(404, "Not Found", "/x"))).toBe("Not available on this node.");
    const conflict = new ApiClientError(409, { error: "claimed", held_by: "codex" } as unknown as string, "/x");
    expect(describeError(conflict)).toBe("claimed — held by codex");
    const invalid = new ApiClientError(422, [{ msg: "bad repo" }] as unknown as string, "/x");
    expect(describeError(invalid)).toBe("bad repo");
    expect(describeError(new ApiClientError(403, "forbidden", "/x"))).toBe("403: forbidden");
  });
});

describe("Staff tab deep link", () => {
  it("opens the run named by ?run= directly", async () => {
    window.history.replaceState({}, "", "/t/staff?run=run-5");
    const run = {
      id: "run-5",
      role: "night-watch",
      provider: "claude",
      model: null,
      machine: "Desk",
      repo: "Tools",
      target_kind: "issue",
      target_ref: "42",
      prompt: "p",
      status: "succeeded",
      requested_by: "op",
      created_at: "2026-09-22T10:00:00Z",
      started_at: null,
      ended_at: null,
      exit_code: 0,
      cost_usd: 0,
      input_tokens: 0,
      output_tokens: 0,
      workdir: "",
      branch: "b",
      transcript_path: "",
      lease_id: "",
      error: "",
      last_line: "",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const body = url === "/api/staff/runs/run-5" ? { run, events: [] } : null;
        return Promise.resolve({
          ok: body !== null,
          status: body ? 200 : 404,
          json: () => Promise.resolve(body ?? { detail: "Not Found" }),
        });
      }),
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute("aria-selected", "true");
  });
});
