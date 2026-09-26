// @vitest-environment jsdom
/**
 * Tests for AdvancedDispatchForm (SC-G5-2, #1498).
 *
 * Acceptance criteria:
 * 1. Each kind renders the right fields (kind decides which target fields show).
 * 2. Dry-run "Preview" posts to the work-request API and shows the plan.
 * 3. Real "Dispatch" posts without dry-run and invokes onDispatched(run_id).
 * 4. A server error keeps the user's input and displays the classified error.
 * 5. Peer-node machine forwarding and Idempotency-Key are preserved.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AdvancedDispatchForm } from "../Staff/AdvancedDispatchForm";

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

const ROSTER = {
  machine: "DeskComputer",
  active_runs: 0,
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
      budget_usd_per_run: 2.0,
      budget_usd_per_day: 10.0,
      budget_max_minutes: 240.0,
      idle_minutes: 20.0,
      permissions: {},
      reports_to: "board-secretary",
      holds: [],
      surface: "dashboard",
      retired: false,
      retired_reason: "",
      dispatchable: true,
      strategy: {},
      persona: {},
      chat: {},
      tools: [],
      group: "specialists",
      max_attempts: 2,
      fallback_providers: [],
      valid: true,
      errors: [],
      source_path: "",
      fleet_actions: [],
      approvals: {},
      defers_to: [],
    },
    {
      name: "cartographer",
      title: "Cartographer",
      summary: "Maps code.",
      playbook: "docs/fleet-cartographer.md",
      providers: ["claude"],
      model: null,
      schedule: null,
      window: null,
      repos: ["Tools"],
      budget_usd_per_run: 1.0,
      budget_usd_per_day: 5.0,
      budget_max_minutes: 60.0,
      idle_minutes: 20.0,
      permissions: {},
      reports_to: "board-secretary",
      holds: [],
      surface: "dashboard",
      retired: false,
      retired_reason: "",
      dispatchable: true,
      strategy: {},
      persona: {},
      chat: {},
      tools: [],
      group: "specialists",
      max_attempts: 2,
      fallback_providers: [],
      valid: true,
      errors: [],
      source_path: "",
      fleet_actions: [],
      approvals: {},
      defers_to: [],
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

describe("AdvancedDispatchForm", () => {
  it("renders kind selector and defaults to staff.dispatch fields", () => {
    render(<AdvancedDispatchForm roster={ROSTER} onDispatched={vi.fn()} />);

    expect(screen.getByLabelText("Kind")).toHaveValue("staff.dispatch");
    expect(screen.getByLabelText("Role")).toBeInTheDocument();
    expect(screen.getByLabelText("Provider")).toBeInTheDocument();
    expect(screen.getByLabelText("Machine")).toHaveValue("local");
    expect(screen.getByLabelText("Repo")).toBeInTheDocument();
    expect(screen.getByLabelText("Issue #")).toBeInTheDocument();
    expect(screen.getByLabelText("PR #")).toBeInTheDocument();
    expect(screen.getByLabelText("Prompt")).toBeInTheDocument();

    // Fields for other kinds must not be visible
    expect(screen.queryByLabelText("Run ID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Ref")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Profile ID")).not.toBeInTheDocument();
  });

  it("switches target fields dynamically based on selected kind", () => {
    render(<AdvancedDispatchForm roster={ROSTER} onDispatched={vi.fn()} />);

    // Switch to ci.remediate
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "ci.remediate" } });

    expect(screen.getByLabelText("Repo")).toBeInTheDocument();
    expect(screen.getByLabelText("Run ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Prompt")).toBeInTheDocument();
    expect(screen.queryByLabelText("Role")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Issue #")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("PR #")).not.toBeInTheDocument();

    // Switch to code_request.dispatch
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "code_request.dispatch" } });
    expect(screen.getByLabelText("Profile ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Ref")).toBeInTheDocument();
    expect(screen.queryByLabelText("Run ID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Issue #")).not.toBeInTheDocument();

    // Switch to issue.act
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "issue.act" } });
    expect(screen.getByLabelText("Role")).toBeInTheDocument();
    expect(screen.getByLabelText("Issue #")).toBeInTheDocument();
    expect(screen.queryByLabelText("PR #")).not.toBeInTheDocument();

    // Switch to pr.act
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "pr.act" } });
    expect(screen.getByLabelText("Role")).toBeInTheDocument();
    expect(screen.getByLabelText("PR #")).toBeInTheDocument();
    expect(screen.queryByLabelText("Issue #")).not.toBeInTheDocument();
  });

  it("preview posts dry-run to work-request API and renders plan", async () => {
    let capturedBody: unknown = null;
    let capturedHeaders: Record<string, string> = {};

    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if ((url === "/api/v1/staff/requests" || url === "/api/staff/requests") && opts?.method === "POST") {
          capturedBody = JSON.parse(opts.body as string);
          capturedHeaders = (opts.headers as Record<string, string>) || {};
          return jsonResponse(200, {
            kind: "staff.dispatch",
            action: "staff.dispatch",
            state: "planned",
            plan: PLAN,
          });
        }
        return jsonResponse(404, { detail: "Not found" });
      }),
    );

    render(<AdvancedDispatchForm roster={ROSTER} onDispatched={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Role"), { target: { value: "night-watch" } });
    fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "UpstreamDrift" } });
    fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "10622" } });
    fireEvent.change(screen.getByLabelText("Machine"), { target: { value: "oglaptop" } });
    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

    await waitFor(() => expect(screen.getByTestId("assign-plan")).toBeInTheDocument());
    expect(screen.getByTestId("plan-consolidation")).toHaveTextContent("serial — open PRs 3 < 6");
    expect(screen.getByTestId("plan-prompt")).toHaveTextContent("You are Night Watch. Fix #10622.");
    expect(screen.getByTestId("plan-branch")).toHaveTextContent("staff/night-watch-10622-preview");

    expect(capturedBody).toMatchObject({
      kind: "staff.dispatch",
      role: "night-watch",
      machine: "oglaptop",
      dry_run: true,
      target: {
        repo: "UpstreamDrift",
        issue: 10622,
      },
    });
    expect(capturedHeaders["X-Requested-With"]).toBe("XMLHttpRequest");
    expect(capturedHeaders["Idempotency-Key"]).toBeTruthy();
  });

  it("dispatch posts real request and triggers onDispatched callback", async () => {
    const onDispatched = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if ((url === "/api/v1/staff/requests" || url === "/api/staff/requests") && opts?.method === "POST") {
          return jsonResponse(201, {
            kind: "staff.dispatch",
            action: "staff.dispatch",
            state: "executed",
            run_id: "run-999",
          });
        }
        return jsonResponse(404, { detail: "Not found" });
      }),
    );

    render(<AdvancedDispatchForm roster={ROSTER} onDispatched={onDispatched} />);

    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "Analyze fleet logs" } });
    fireEvent.click(screen.getByRole("button", { name: /^dispatch$/i }));

    await waitFor(() => expect(onDispatched).toHaveBeenCalledWith("run-999"));
  });

  it("keeps user input on server error and displays classified error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, opts?: RequestInit) => {
        if ((url === "/api/v1/staff/requests" || url === "/api/staff/requests") && opts?.method === "POST") {
          return jsonResponse(503, {
            error: {
              code: "peer_unreachable",
              message: "Peer machine 'oglaptop' is currently unreachable",
            },
          });
        }
        return jsonResponse(404, { detail: "Not found" });
      }),
    );

    render(<AdvancedDispatchForm roster={ROSTER} onDispatched={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "Tools" } });
    fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "45" } });
    fireEvent.change(screen.getByLabelText("Machine"), { target: { value: "oglaptop" } });
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "Custom urgent task" } });

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

    await waitFor(() =>
      expect(screen.getByText(/Peer machine 'oglaptop' is currently unreachable/)).toBeInTheDocument(),
    );

    // Verify all user inputs remain intact
    expect(screen.getByLabelText("Repo")).toHaveValue("Tools");
    expect(screen.getByLabelText("Issue #")).toHaveValue("45");
    expect(screen.getByLabelText("Machine")).toHaveValue("oglaptop");
    expect(screen.getByLabelText("Prompt")).toHaveValue("Custom urgent task");
  });
});
