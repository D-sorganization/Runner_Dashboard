// @vitest-environment jsdom
/**
 * Tests for AdvancedDispatchForm (SC-G5-2, #1498).
 *
 * Acceptance criteria:
 * 1. Only backend-supported kinds are offered; each renders its own target fields.
 * 2. Dry-run preview shows the plan.
 * 3. Server error keeps the user's input and shows the classified error.
 * 4. Dispatch posts for real and invokes onDispatched; a request awaiting approval is shown.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdvancedDispatchForm } from "../Staff/AdvancedDispatchForm";
import { REQUEST_KINDS } from "../Staff/requestKinds";
import type { RosterResponse } from "../Staff/staffApi";

afterEach(() => {
  cleanup();
});

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const ROSTER: RosterResponse = {
  machine: "DeskComputer",
  active_runs: 1,
  providers: { claude: true, codex: false },
  roles: [
    {
      name: "night-watch",
      title: "Night Watch",
      summary: "Sweeps red main overnight.",
      playbook: "docs/fleet-night-watch.md",
      providers: ["claude", "codex"],
      model: null,
      schedule: "0 2 * * *",
      window: "02:00-06:00",
      repos: ["UpstreamDrift"],
      budget: { usd_per_run: 5, usd_per_day: 20 },
      permissions: {},
      reports_to: null,
      holds: [],
      surface: null,
      retired: false,
      dispatchable: true,
      source_path: "staff/roles/night-watch.yml",
      active_runs: 1,
    },
    {
      name: "pr-remediator",
      title: "PR Remediator",
      summary: "",
      playbook: "",
      providers: ["claude"],
      model: null,
      schedule: "0 3 * * *",
      window: null,
      repos: ["UpstreamDrift"],
      budget: { usd_per_run: null, usd_per_day: null },
      permissions: {},
      reports_to: null,
      holds: [],
      surface: null,
      retired: false,
      strategy: { consolidate_when: { open_prs: 6, utilisation_pct: 70 } },
      dispatchable: true,
      source_path: "staff/roles/pr-remediator.yml",
      active_runs: 0,
    },
    {
      name: "archivist",
      title: "Archivist",
      summary: "",
      playbook: "",
      providers: ["codex"],
      model: null,
      schedule: null,
      window: null,
      repos: [],
      budget: { usd_per_run: null, usd_per_day: null },
      permissions: {},
      reports_to: null,
      holds: [],
      surface: null,
      retired: true,
      dispatchable: false,
      source_path: "staff/roles/archivist.yml",
      active_runs: 0,
    },
  ],
};

const PLAN = {
  role: "night-watch",
  provider: "claude",
  model: null,
  repo: "UpstreamDrift",
  target_kind: "issue",
  target_ref: "10622",
  prompt: "You are Night Watch. Fix #10622.",
  argv: ["claude", "-p", "--output-format", "stream-json"],
  branch: "staff/night-watch-10622-preview",
  lease_ritual: true,
  consolidation: { mode: "serial", reason: "open PRs 3 < 6", threshold: { open_prs: 6 } },
};

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

function stubFetch(handler?: (url: string, opts?: RequestInit) => { status: number; body: unknown } | undefined) {
  const calls: [string, RequestInit | undefined][] = [];
  const fn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push([url, init]);
    const res = handler?.(url, init);
    if (res) return jsonResponse(res.status, res.body);
    return jsonResponse(404, { detail: "not mocked: " + url });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

describe("AdvancedDispatchForm", () => {
  describe("kind decides target fields", () => {
    it("offers only the kinds the backend accepts", () => {
      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={() => {}} />);
      const kinds = Array.from((screen.getByLabelText("Kind") as HTMLSelectElement).options).map((o) => o.value);
      expect(kinds).toEqual(REQUEST_KINDS.map((k) => k.id));
      expect(kinds).toEqual(["staff.dispatch"]);
    });

    it("renders repo, issue and PR for staff.dispatch, and no run id", () => {
      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={() => {}} />);
      expect(screen.getByLabelText("Kind")).toHaveValue("staff.dispatch");
      expect(screen.getByLabelText("Repo")).toBeInTheDocument();
      expect(screen.getByLabelText("Issue #")).toBeInTheDocument();
      expect(screen.getByLabelText("PR #")).toBeInTheDocument();
      expect(screen.queryByLabelText("Run ID")).not.toBeInTheDocument();
    });
  });

  describe("roles and providers", () => {
    it("lists only dispatchable, non-retired roles", () => {
      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={() => {}} />);
      const select = screen.getByLabelText("Role") as HTMLSelectElement;
      const options = Array.from(select.options).map((o) => o.value);
      expect(options).toEqual(["night-watch", "pr-remediator"]);
      expect(options).not.toContain("archivist");
    });

    it("sorts provider options with installed first", () => {
      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={() => {}} />);
      const select = screen.getByLabelText("Provider") as HTMLSelectElement;
      const options = Array.from(select.options).map((o) => o.value);
      expect(options).toEqual(["claude", "codex"]);
    });

    it("respects initialRole prop", () => {
      render(<AdvancedDispatchForm roster={ROSTER} initialRole="pr-remediator" onDispatched={() => {}} />);
      expect(screen.getByLabelText("Role")).toHaveValue("pr-remediator");
    });
  });

  describe("dry-run preview", () => {
    it("posts dry_run: true to /api/v1/staff/requests and shows the plan", async () => {
      const calls = stubFetch((url, opts) => {
        if (url === "/api/v1/staff/requests" && opts?.method === "POST") {
          return {
            status: 200,
            body: {
              state: "planned",
              kind: "staff.dispatch",
              action: "staff.dispatch",
              plan: {
                dry_run: true,
                machine: "DeskComputer",
                forwarded_to: null,
                plan: PLAN,
              },
            },
          };
        }
        return undefined;
      });

      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={() => {}} />);
      fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "UpstreamDrift" } });
      fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "10622" } });
      fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

      await waitFor(() => expect(screen.getByTestId("assign-plan")).toBeInTheDocument());
      expect(screen.getByTestId("plan-machine")).toHaveTextContent("DeskComputer");
      expect(screen.getByTestId("plan-branch")).toHaveTextContent("staff/night-watch-10622-preview");
      expect(screen.getByTestId("plan-consolidation")).toHaveTextContent("serial — open PRs 3 < 6");
      expect(screen.getByTestId("plan-argv")).toHaveTextContent("claude -p --output-format stream-json");
      expect(screen.getByTestId("plan-prompt")).toHaveTextContent("You are Night Watch. Fix #10622.");

      expect(calls).toHaveLength(1);
      const [url, init] = calls[0];
      expect(url).toBe("/api/v1/staff/requests");
      const headers = init?.headers as Record<string, string>;
      expect(headers["X-Requested-With"]).toBe("XMLHttpRequest");
      expect(headers["Idempotency-Key"]).toBeDefined();
      const body = JSON.parse(init?.body as string);
      expect(body).toEqual({
        kind: "staff.dispatch",
        role: "night-watch",
        provider: "claude",
        model: null,
        machine: "local",
        dry_run: true,
        prompt: "",
        target: {
          repo: "UpstreamDrift",
          issue: 10622,
          pr: null,
          ref: "",
        },
      });
    });
  });

  describe("dispatch execution", () => {
    it("posts dry_run: false, invokes onDispatched, and renders the staff link", async () => {
      const onDispatched = vi.fn();
      stubFetch((url, opts) => {
        if (url === "/api/v1/staff/requests" && opts?.method === "POST") {
          return {
            status: 201,
            body: {
              state: "executed",
              kind: "staff.dispatch",
              action: "staff.dispatch",
              run_id: "run-77",
              result: {
                run_id: "run-77",
                machine: "local",
              },
            },
          };
        }
        return undefined;
      });

      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={onDispatched} />);
      fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "fix everything" } });
      fireEvent.click(screen.getByRole("button", { name: /^dispatch$/i }));

      await waitFor(() => expect(onDispatched).toHaveBeenCalledWith("run-77"));
    });

    it("shows a request that awaits approval instead of dropping it", async () => {
      const onDispatched = vi.fn();
      stubFetch((url, opts) =>
        url === "/api/v1/staff/requests" && opts?.method === "POST"
          ? {
              status: 202,
              body: { state: "approval_required", kind: "staff.dispatch", action: "staff.dispatch", approval: "needs owner" },
            }
          : undefined,
      );

      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={onDispatched} />);
      fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "fix everything" } });
      fireEvent.click(screen.getByRole("button", { name: /^dispatch$/i }));

      await waitFor(() => expect(screen.getByTestId("dispatch-notice")).toHaveTextContent("Awaiting approval: needs owner"));
      expect(onDispatched).not.toHaveBeenCalled();
      expect(screen.getByLabelText("Prompt")).toHaveValue("fix everything");
    });
  });

  describe("error handling", () => {
    it("keeps user input on server error and displays the classified error", async () => {
      stubFetch((url, opts) => {
        if (url === "/api/v1/staff/requests" && opts?.method === "POST") {
          return {
            status: 429,
            body: {
              error: {
                code: "rate_limited",
                message: "dispatch rate limit exceeded",
                retryable: true,
              },
            },
          };
        }
        return undefined;
      });

      render(<AdvancedDispatchForm roster={ROSTER} onDispatched={() => {}} />);
      fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "Runner_Dashboard" } });
      fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "1498" } });
      fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "my important prompt" } });
      fireEvent.change(screen.getByLabelText("Machine"), { target: { value: "remote-node" } });

      fireEvent.click(screen.getByRole("button", { name: /^dispatch$/i }));

      await waitFor(() => expect(screen.getByTestId("dispatch-error")).toBeInTheDocument());
      expect(screen.getByTestId("dispatch-error")).toHaveTextContent("dispatch rate limit exceeded");

      // Verify user input is kept
      expect(screen.getByLabelText("Repo")).toHaveValue("Runner_Dashboard");
      expect(screen.getByLabelText("Issue #")).toHaveValue("1498");
      expect(screen.getByLabelText("Prompt")).toHaveValue("my important prompt");
      expect(screen.getByLabelText("Machine")).toHaveValue("remote-node");
    });
  });
});
