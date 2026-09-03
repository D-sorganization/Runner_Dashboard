// @vitest-environment jsdom
/**
 * Behaviour tests for pages/RemediationTab.tsx — the full desktop Remediation
 * tab extracted from the legacy App.tsx monolith (decomposition #836, pass 11).
 *
 * Covers, against the legacy behaviour this 1:1 port preserves:
 * 1. Default "Automations" sub-tab renders the manual-dispatch + config view.
 * 2. Empty / error states for the manual-dispatch list.
 * 3. A failed run renders with its repo/workflow/branch label and action links.
 * 4. Provider select + Preview / Dispatch buttons thread the run id and call
 *    the supplied callbacks; Dispatch is gated by the plan `accepted` flag.
 * 5. Inline-editable Loop guard + Default provider stat cards save via onSaveConfig.
 * 6. Workflow-type routing rules render and edit through onSaveConfig.
 * 7. Remediation history, plan preview (blocked + allowed), and provider
 *    availability panels render their data.
 * 8. The Jules workflow-health "Run" button POSTs and flashes a success banner.
 * 9. Tapping a run opens the mobile action sheet; its dispatch path fires onDispatch.
 * 10. Switching to the PRs / Issues sub-tabs mounts the self-contained sub-views.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RemediationTab, type RemediationTabProps } from "../RemediationTab";

afterEach(cleanup);
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const FAILED_RUN = {
  id: 4242,
  conclusion: "failure",
  name: "CI Standard",
  repository: { name: "alpha", html_url: "https://github.com/D-sorganization/alpha" },
  head_branch: "fix/thing",
  created_at: "2026-06-01T12:34:56Z",
  html_url: "https://github.com/D-sorganization/alpha/actions/runs/4242",
};

const CONFIG = {
  policy: {
    default_provider: "codex_cli",
    max_same_failure_attempts: 4,
    workflow_type_rules: {
      lint: {
        label: "Lint failures",
        match_terms: ["ruff", "eslint"],
        dispatch_mode: "auto",
        provider_id: "codex_cli",
        fallback_providers: ["jules_api"],
      },
    },
  },
  providers: {
    jules_api: { label: "Jules API", notes: "cloud" },
    codex_cli: { label: "Codex CLI", notes: "local" },
  },
  availability: {
    jules_api: { available: true, status: "ready" },
    codex_cli: { available: false, status: "degraded" },
  },
};

function baseProps(
  overrides: Partial<RemediationTabProps> = {},
): RemediationTabProps {
  return {
    config: CONFIG,
    workflows: { workflows: [] },
    runs: [FAILED_RUN],
    loading: false,
    error: null,
    selectedRunId: null,
    setSelectedRunId: vi.fn(),
    provider: "jules_api",
    setProvider: vi.fn(),
    model: "",
    setModel: vi.fn(),
    plan: null,
    dispatchState: null,
    onRefresh: vi.fn(),
    onSaveConfig: vi.fn(() => Promise.resolve()),
    onPreview: vi.fn(),
    onDispatch: vi.fn(),
    history: [],
    principalName: "tester",
    ...overrides,
  };
}

describe("RemediationTab — automations view", () => {
  it("renders the three-way sub-tab strip defaulting to Automations", () => {
    render(<RemediationTab {...baseProps()} />);
    const tablist = screen.getByRole("tablist");
    expect(within(tablist).getByText("Automations")).toBeInTheDocument();
    expect(within(tablist).getByText("PRs")).toBeInTheDocument();
    expect(within(tablist).getByText("Issues")).toBeInTheDocument();
    expect(screen.getByText("Manual Dispatch")).toBeInTheDocument();
  });

  it("shows the empty placeholder when there are no failed runs", () => {
    render(<RemediationTab {...baseProps({ runs: [] })} />);
    expect(
      screen.getByText("No failed runs in the current dashboard sample."),
    ).toBeInTheDocument();
  });

  it("renders the inline error banner when error is set", () => {
    render(<RemediationTab {...baseProps({ error: "boom" })} />);
    expect(screen.getByText("boom")).toBeInTheDocument();
  });

  it("renders a failed run with its repo/workflow/branch label", () => {
    render(<RemediationTab {...baseProps()} />);
    expect(
      screen.getByText(/alpha · CI Standard · fix\/thing #4242/),
    ).toBeInTheDocument();
    // Action links present.
    expect(screen.getByText("↗ Run")).toBeInTheDocument();
    expect(screen.getByText("↗ Logs")).toBeInTheDocument();
  });

  it("Preview threads the run id and calls onPreview", () => {
    const props = baseProps();
    render(<RemediationTab {...props} />);
    fireEvent.click(screen.getByText("Preview"));
    expect(props.setSelectedRunId).toHaveBeenCalledWith("4242");
    expect(props.onPreview).toHaveBeenCalledWith(
      expect.objectContaining({ id: 4242 }),
    );
  });

  it("gates Dispatch on the plan accepted flag", () => {
    // Selected run with no accepting plan -> disabled.
    const blocked = baseProps({ selectedRunId: "4242", plan: { decision: { accepted: false } } });
    const { unmount } = render(<RemediationTab {...blocked} />);
    expect(screen.getByText("Dispatch").closest("button")).toBeDisabled();
    unmount();

    const allowed = baseProps({ selectedRunId: "4242", plan: { decision: { accepted: true } } });
    render(<RemediationTab {...allowed} />);
    const dispatchBtn = screen.getByText("Dispatch").closest("button")!;
    expect(dispatchBtn).not.toBeDisabled();
    fireEvent.click(dispatchBtn);
    expect(allowed.onDispatch).toHaveBeenCalledWith(
      expect.objectContaining({ id: 4242 }),
    );
  });

  it("changing the provider select threads the run id and calls setProvider", () => {
    const props = baseProps();
    render(<RemediationTab {...props} />);
    const select = screen
      .getByText("Manual Dispatch")
      .closest(".section")!
      .querySelector("select")!;
    fireEvent.change(select, { target: { value: "codex_cli" } });
    expect(props.setProvider).toHaveBeenCalledWith("codex_cli");
  });
});

describe("RemediationTab — inline editable stats", () => {
  it("edits and saves the loop guard via onSaveConfig", () => {
    const props = baseProps();
    render(<RemediationTab {...props} />);
    fireEvent.click(screen.getByText("Loop guard"));
    const input = screen.getByDisplayValue("4");
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onSaveConfig).toHaveBeenCalledWith(
      expect.objectContaining({ max_same_failure_attempts: 7 }),
    );
  });

  it("edits and saves the default provider via onSaveConfig", () => {
    const props = baseProps();
    render(<RemediationTab {...props} />);
    const card = screen.getByText("Default provider").closest(".stat-card")!;
    fireEvent.click(screen.getByText("Default provider"));
    // The edit select (scoped to this stat card) shows the current default.
    const select = within(card).getByRole("combobox");
    fireEvent.change(select, { target: { value: "jules_api" } });
    expect(props.onSaveConfig).toHaveBeenCalledWith(
      expect.objectContaining({ default_provider: "jules_api" }),
    );
  });
});

describe("RemediationTab — config + panels", () => {
  it("renders workflow-type routing rules and edits dispatch mode", () => {
    const props = baseProps();
    render(<RemediationTab {...props} />);
    expect(screen.getByText("Lint failures")).toBeInTheDocument();
    expect(screen.getByText("ruff, eslint")).toBeInTheDocument();
    const modeSelect = screen.getByDisplayValue("Auto");
    fireEvent.change(modeSelect, { target: { value: "manual" } });
    fireEvent.click(screen.getByText("Save routing"));
    expect(props.onSaveConfig).toHaveBeenCalled();
  });

  it("renders provider availability with status badges", () => {
    render(<RemediationTab {...baseProps()} />);
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByText("degraded")).toBeInTheDocument();
  });

  it("renders remediation history entries", () => {
    const history = [
      {
        repository: "alpha",
        workflow_name: "CI",
        status: "dispatched",
        timestamp: "2026-06-01T01:02:03Z",
        provider: "jules_api",
        branch: "main",
        run_id: 9,
      },
    ];
    render(<RemediationTab {...baseProps({ history })} />);
    expect(screen.getByText("alpha · CI")).toBeInTheDocument();
    expect(screen.getByText("dispatched")).toBeInTheDocument();
  });

  it("renders the plan preview blocked + allowed states", () => {
    const { unmount } = render(
      <RemediationTab
        {...baseProps({
          plan: { decision: { accepted: false, reason: "loop guard", attempt_count: 2 } },
        })}
      />,
    );
    expect(screen.getByText("blocked")).toBeInTheDocument();
    expect(screen.getByText("loop guard")).toBeInTheDocument();
    unmount();

    render(
      <RemediationTab
        {...baseProps({
          plan: {
            decision: {
              accepted: true,
              provider_id: "codex_cli",
              prompt_preview: "do the thing",
            },
          },
        })}
      />,
    );
    expect(screen.getByText("dispatch allowed")).toBeInTheDocument();
    expect(screen.getByText("do the thing")).toBeInTheDocument();
  });

  it("shows the dispatch in-flight status tile", () => {
    render(
      <RemediationTab {...baseProps({ dispatchState: { note: "submitted" } })} />,
    );
    expect(screen.getByText("Agent working")).toBeInTheDocument();
    expect(screen.getByText("submitted")).toBeInTheDocument();
  });
});

describe("RemediationTab — agent workflow health", () => {
  it("renders a manual workflow with a Run button that POSTs and flashes success", async () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response),
    );
    vi.stubGlobal("fetch", fetchFn);

    const workflows = {
      summary: "all good",
      workflows: [
        {
          workflow_file: "Agent-Lease-Reaper.yml",
          workflow_name: "Agent Lease Reaper",
          trigger_type: "manual",
          manual_dispatch: true,
          scheduled: false,
          workflow_run_trigger: false,
          issues: [],
        },
      ],
    };
    render(<RemediationTab {...baseProps({ workflows })} />);
    expect(screen.getByText("all good")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Run"));
    await waitFor(() => {
      expect(fetchFn).toHaveBeenCalledWith(
        "/api/agent-remediation/dispatch-jules",
        expect.objectContaining({ method: "POST" }),
      );
    });
    expect(await screen.findByText("Dispatched Agent-Lease-Reaper.yml")).toBeInTheDocument();
  });
});

describe("RemediationTab — mobile action sheet", () => {
  it("opens the sheet on run click and dispatches from it", () => {
    const props = baseProps();
    render(<RemediationTab {...props} />);
    // Click the run card (not a button inside it).
    fireEvent.click(screen.getByText(/alpha · CI Standard · fix\/thing #4242/));
    const sheet = screen.getByRole("dialog");
    expect(sheet).toBeInTheDocument();
    expect(props.setSelectedRunId).toHaveBeenCalledWith("4242");
    // Dispatch button label includes the recommended provider.
    fireEvent.click(within(sheet).getByText(/^Dispatch /));
    expect(props.onDispatch).toHaveBeenCalledWith(
      expect.objectContaining({ id: 4242 }),
    );
  });
});

describe("RemediationTab — sub-tab switching", () => {
  it("mounts the PRs sub-tab when selected", () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ prs: [] }) } as Response),
    );
    vi.stubGlobal("fetch", fetchFn);
    render(<RemediationTab {...baseProps()} />);
    fireEvent.click(within(screen.getByRole("tablist")).getByText("PRs"));
    // The automations-only "Manual Dispatch" heading is gone.
    expect(screen.queryByText("Manual Dispatch")).not.toBeInTheDocument();
  });

  it("mounts the Issues sub-tab when selected", () => {
    const fetchFn = vi.fn(() =>
      Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ issues: [] }),
      } as Response),
    );
    vi.stubGlobal("fetch", fetchFn);
    render(<RemediationTab {...baseProps()} />);
    fireEvent.click(within(screen.getByRole("tablist")).getByText("Issues"));
    expect(screen.queryByText("Manual Dispatch")).not.toBeInTheDocument();
  });
});
