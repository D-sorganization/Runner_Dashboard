// @vitest-environment jsdom
/**
 * Behaviour tests for pages/Staff — the Staff tab (issue #1198, epic #1192).
 *
 * Covers:
 * 1. Roster renders one card per role with installed-provider badges.
 * 2. Board shows spend today and per-machine running/queued counts.
 * 3. Assign "Preview" POSTs dry_run:true with the CSRF header and shows the plan.
 * 4. Run detail shows the stored events and appends SSE events while alive.
 * 5. Cancel POSTs /api/staff/runs/{id}/cancel with the CSRF header.
 * 6. Holds 404 renders the "unavailable" state, not an error.
 * 7. Board lists late/dead scheduled roles as liveness alerts (#1209).
 * 8. PR consolidation (#1213): roster shows the threshold, the plan shows the decision,
 *    run log and run detail show the outcome.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { queryClient } from "../../hooks/usePollingQueries";
import { StaffPage } from "../Staff";

afterEach(() => {
  cleanup();
  queryClient.clear();
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

const RUN = {
  id: "run-1",
  role: "night-watch",
  provider: "claude",
  model: null,
  machine: "DeskComputer",
  repo: "UpstreamDrift",
  target_kind: "issue",
  target_ref: "10622",
  prompt: "Fix the thing",
  status: "running",
  requested_by: "operator",
  created_at: "2026-09-22T10:00:00Z",
  started_at: "2026-09-22T10:00:05Z",
  ended_at: null,
  exit_code: null,
  cost_usd: 0.42,
  input_tokens: 10,
  output_tokens: 20,
  workdir: "",
  branch: "staff/night-watch-10622-run-1",
  transcript_path: "",
  lease_id: "",
  error: "",
  last_line: "working",
};

const CONSOLIDATED_RUN = {
  ...RUN,
  id: "run-9",
  role: "pr-remediator",
  status: "succeeded",
  strategy_mode: "consolidate",
  outcome: "consolidated 12 PRs into #1801",
};

const BOARD = {
  machine: "DeskComputer",
  generated_at: "2026-09-22T10:01:00Z",
  running: [RUN],
  queued: [{ ...RUN, id: "run-2", status: "queued", machine: "ControlTower" }],
  recent: [],
  spend_today_usd: 3.5,
  providers: { claude: true, codex: false },
};

const EVENTS = [
  { seq: 1, ts: "2026-09-22T10:00:00Z", kind: "queued", text: "queued on DeskComputer for claude" },
  { seq: 2, ts: "2026-09-22T10:00:05Z", kind: "start", text: "claude in /tmp/wt" },
];

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

type Handler = (url: string, opts?: RequestInit) => { status: number; body: unknown } | undefined;

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** fetch stub routed by URL; unknown routes 404 so orthogonal panels degrade. */
function stubFetch(extra: Handler = () => undefined) {
  const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
    const norm = url.replace(/^\/api\/v1\/staff/, "/api/staff");
    const custom = extra(norm, opts) ?? extra(url, opts);
    if (custom) return jsonResponse(custom.status, custom.body);
    if (norm === "/api/staff/roster") return jsonResponse(200, ROSTER);
    if (norm === "/api/staff/board") return jsonResponse(200, BOARD);
    if (norm.startsWith("/api/staff/runs?")) {
      return jsonResponse(200, { runs: [RUN, CONSOLIDATED_RUN], count: 2 });
    }
    if (norm === "/api/staff/runs/run-1") return jsonResponse(200, { run: RUN, events: EVENTS });
    if (norm === "/api/staff/runs/run-9") return jsonResponse(200, { run: CONSOLIDATED_RUN, events: [] });
    return jsonResponse(404, { detail: "Not Found" });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** Minimal EventSource double that records listeners so a test can push frames. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, EventListener[]>();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, cb: EventListener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), cb]);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    for (const cb of this.listeners.get(type) ?? []) cb({ data: JSON.stringify(data) } as unknown as Event);
  }
}

function postCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([, opts]) => (opts as RequestInit | undefined)?.method === "POST") as [
    string,
    RequestInit,
  ][];
}

describe("StaffPage", () => {
  it("renders roster cards with installed-provider badges and state", async () => {
    stubFetch();
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("role-card-night-watch")).toBeInTheDocument());
    const card = screen.getByTestId("role-card-night-watch");
    expect(within(card).getByText("Night Watch")).toBeInTheDocument();
    expect(within(card).getByText("claude · installed")).toBeInTheDocument();
    expect(within(card).getByText("codex")).toBeInTheDocument();
    expect(within(card).getByText("0 2 * * * (02:00-06:00)")).toBeInTheDocument();
    expect(within(card).getByText("$5.00/run · $20.00/day")).toBeInTheDocument();
    expect(screen.getByTestId("role-active-night-watch")).toHaveTextContent("1");
    expect(within(card).getByText("dispatchable")).toBeInTheDocument();
    const retired = screen.getByTestId("role-card-archivist");
    expect(within(retired).getByText("retired")).toBeInTheDocument();
    expect(within(retired).getByRole("button", { name: /assign/i })).toBeDisabled();
  });

  it("roster shows the consolidation threshold only for roles with a strategy", async () => {
    stubFetch();
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("role-card-pr-remediator")).toBeInTheDocument());
    expect(screen.getByTestId("role-strategy-pr-remediator")).toHaveTextContent(
      "consolidate when open PRs ≥ 6 and utilisation ≥ 70%",
    );
    expect(screen.queryByTestId("role-strategy-night-watch")).not.toBeInTheDocument();
  });

  it("run log and run detail show the consolidation outcome when present", async () => {
    stubFetch();
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Runs" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(screen.getByTestId("run-row-run-9")).toBeInTheDocument());
    expect(screen.getByTestId("run-outcome-run-9")).toHaveTextContent("consolidated 12 PRs into #1801");
    expect(screen.getByTestId("run-outcome-run-1")).toHaveTextContent("—");
    fireEvent.click(screen.getByTestId("run-row-run-9"));
    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.getByTestId("run-strategy")).toHaveTextContent("consolidate");
    expect(screen.getByTestId("run-outcome")).toHaveTextContent("consolidated 12 PRs into #1801");
  });

  it("board shows spend today and per-machine running/queued counts", async () => {
    stubFetch();
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("board-spend")).toHaveTextContent("$3.50"));
    expect(screen.getByTestId("board-machine-DeskComputer")).toHaveTextContent("1 running");
    expect(screen.getByTestId("board-machine-ControlTower")).toHaveTextContent("1 queued");
  });

  it("board renders with real dict spend fixture from live node without crashing (#1289)", async () => {
    const liveSpend = { claude: 0.28, codex: 0.32, total: 0.60 };
    stubFetch((url) =>
      url === "/api/staff/board" ? { status: 200, body: { ...BOARD, spend_today_usd: liveSpend } } : undefined,
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("board-spend")).toHaveTextContent("$0.60"));
    expect(screen.getByTestId("board-spend")).toHaveAttribute("title", "claude: $0.28 · codex: $0.32");
    expect(screen.getByTestId("board-machine-DeskComputer")).toHaveTextContent("1 running");
  });

  it("board renders with missing spend showing em dash without crashing (#1289)", async () => {
    stubFetch((url) =>
      url === "/api/staff/board" ? { status: 200, body: { ...BOARD, spend_today_usd: undefined } } : undefined,
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("board-spend")).toHaveTextContent("—"));
    expect(screen.getByTestId("board-machine-DeskComputer")).toHaveTextContent("1 running");
  });

  it("board renders with NaN spend showing em dash without crashing (#1289)", async () => {
    stubFetch((url) =>
      url === "/api/staff/board" ? { status: 200, body: { ...BOARD, spend_today_usd: NaN } } : undefined,
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("board-spend")).toHaveTextContent("—"));
    expect(screen.getByTestId("board-machine-DeskComputer")).toHaveTextContent("1 running");
  });

  it("board lists late and dead scheduled roles as liveness alerts", async () => {
    const alerts = [
      {
        role: "night-watch",
        schedule: "0 2 * * *",
        status: "dead",
        last_success: null,
        last_attempt: null,
        last_fired: "2026-09-01T09:00:00Z",
        next_fire: "2026-09-23T09:00:00Z",
        expected_interval_seconds: 86400,
        age_seconds: 1_800_000,
        machine: "ControlTower",
      },
    ];
    stubFetch((url) =>
      url === "/api/staff/board" ? { status: 200, body: { ...BOARD, liveness_alerts: alerts } } : undefined,
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("board-liveness-alerts")).toBeInTheDocument());
    const list = screen.getByTestId("board-liveness-alerts");
    expect(within(list).getByText("dead")).toBeInTheDocument();
    expect(list).toHaveTextContent("night-watch on ControlTower");
    expect(list).toHaveTextContent("last success never");
  });

  it("board hides the liveness list when nothing is late or dead", async () => {
    stubFetch((url) =>
      url === "/api/staff/board"
        ? { status: 200, body: { ...BOARD, liveness: [{ role: "hourly", status: "ok" }], liveness_alerts: [] } }
        : undefined,
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByTestId("board-spend")).toHaveTextContent("$3.50"));
    expect(screen.queryByTestId("board-liveness-alerts")).not.toBeInTheDocument();
  });

  it("assign lists only dispatchable, non-retired roles", async () => {
    const chatOnly = { ...ROSTER.roles[0], name: "barb", title: "Barb", dispatchable: false, surface: "grok-chat" };
    stubFetch((url) =>
      url === "/api/staff/roster" ? { status: 200, body: { ...ROSTER, roles: [...ROSTER.roles, chatOnly] } } : undefined,
    );
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Assign" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Assign" }));
    await waitFor(() => expect(screen.getByLabelText("Role")).toHaveValue("night-watch"));
    const options = Array.from((screen.getByLabelText("Role") as HTMLSelectElement).options).map((o) => o.value);
    expect(options).not.toContain("barb");
    expect(options).not.toContain("archivist");
  });

  it("assign preview posts dry_run with CSRF header and shows the plan", async () => {
    const fetchMock = stubFetch((url, opts) => {
      if ((url === "/api/staff/night-watch/run" || url === "/api/v1/staff/night-watch/run") && opts?.method === "POST") {
        return { status: 200, body: { dry_run: true, plan: PLAN, machine: "DeskComputer" } };
      }
      return undefined;
    });
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Assign" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Assign" }));
    await waitFor(() => expect(screen.getByLabelText("Role")).toHaveValue("night-watch"));
    // Provider list is limited to the role's providers, installed first.
    const providerSelect = screen.getByLabelText("Provider") as HTMLSelectElement;
    expect(Array.from(providerSelect.options).map((o) => o.value)).toEqual(["claude", "codex"]);
    expect(screen.getByLabelText("Machine")).toHaveValue("local");
    fireEvent.change(screen.getByLabelText("Repo"), { target: { value: "UpstreamDrift" } });
    fireEvent.change(screen.getByLabelText("Issue #"), { target: { value: "10622" } });
    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));

    await waitFor(() => expect(screen.getByTestId("assign-plan")).toBeInTheDocument());
    expect(screen.getByTestId("plan-consolidation")).toHaveTextContent("serial — open PRs 3 < 6");
    expect(screen.getByTestId("plan-prompt")).toHaveTextContent("You are Night Watch. Fix #10622.");
    expect(screen.getByTestId("plan-argv")).toHaveTextContent("claude -p --output-format stream-json");
    expect(screen.getByTestId("plan-branch")).toHaveTextContent("staff/night-watch-10622-preview");

    const [url, opts] = postCalls(fetchMock)[0];
    expect(url).toBe("/api/v1/staff/night-watch/run");
    expect((opts.headers as Record<string, string>)["X-Requested-With"]).toBe("XMLHttpRequest");
    expect(JSON.parse(opts.body as string)).toMatchObject({
      dry_run: true,
      provider: "claude",
      repo: "UpstreamDrift",
      issue: 10622,
      pr: null,
      machine: "local",
    });
  });

  it("dispatch posts for real and navigates to the new run", async () => {
    stubFetch((url, opts) => {
      if ((url === "/api/staff/night-watch/run" || url === "/api/v1/staff/night-watch/run") && opts?.method === "POST") {
        return { status: 200, body: { dry_run: false, run: RUN, machine: "DeskComputer" } };
      }
      return undefined;
    });
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Assign" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Assign" }));
    await waitFor(() => expect(screen.getByLabelText("Role")).toHaveValue("night-watch"));
    fireEvent.change(screen.getByLabelText("Prompt"), { target: { value: "do a thing" } });
    fireEvent.click(screen.getByRole("button", { name: /^dispatch$/i }));
    await waitFor(() => expect(screen.getByTestId("run-detail")).toBeInTheDocument());
    expect(screen.getByTestId("run-status")).toHaveTextContent("running");
  });

  it("run detail shows stored events and appends streamed ones", async () => {
    stubFetch();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Runs" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(screen.getByTestId("run-row-run-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("run-row-run-1"));
    await waitFor(() => expect(screen.getByTestId("run-events")).toHaveTextContent("[start] claude in /tmp/wt"));
    expect(screen.getByTestId("run-events")).toHaveTextContent("[queued] queued on DeskComputer");

    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe("/api/v1/staff/runs/run-1/stream?after=2");
    source.emit("text", { seq: 3, ts: "2026-09-22T10:00:10Z", kind: "text", text: "hello from claude" });
    await waitFor(() => expect(screen.getByTestId("run-events")).toHaveTextContent("[text] hello from claude"));
  });

  it("cancel posts to the cancel route with the CSRF header", async () => {
    let cancelled = false;
    const fetchMock = stubFetch((url, opts) => {
      if ((url === "/api/staff/runs/run-1/cancel" || url === "/api/v1/staff/runs/run-1/cancel") && opts?.method === "POST") {
        cancelled = true;
        return { status: 200, body: { cancelled: true, run: { ...RUN, status: "cancelled" } } };
      }
      if ((url === "/api/staff/runs/run-1" || url === "/api/v1/staff/runs/run-1") && cancelled) {
        return { status: 200, body: { run: { ...RUN, status: "cancelled" }, events: EVENTS } };
      }
      return undefined;
    });
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Runs" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Runs" }));
    await waitFor(() => expect(screen.getByTestId("run-row-run-1")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("run-row-run-1"));
    await waitFor(() => expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      const call = postCalls(fetchMock).find(([url]) => url === "/api/v1/staff/runs/run-1/cancel");
      expect(call).toBeTruthy();
      expect((call![1].headers as Record<string, string>)["X-Requested-With"]).toBe("XMLHttpRequest");
    });
    await waitFor(() => expect(screen.getByTestId("run-status")).toHaveTextContent("cancelled"));
  });

  it("holds 404 renders the unavailable state", async () => {
    stubFetch();
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Holds" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Holds" }));
    await waitFor(() => expect(screen.getByTestId("holds-unavailable")).toBeInTheDocument());
    expect(screen.getByText(/holds unavailable/i)).toBeInTheDocument();
    expect(document.querySelector(".empty-state")).not.toHaveAttribute("data-variant", "error");
  });

  it("holds render and save via PUT with the CSRF header", async () => {
    const HOLD = {
      id: "h1",
      text: "No dispatch during release",
      set_on: "2026-09-20",
      lifted_when: "after 4.10",
      applies_to: ["night-watch"],
      active: true,
    };
    const fetchMock = stubFetch((url, opts) => {
      if ((url === "/api/staff/holds" || url === "/api/v1/staff/holds") && (opts?.method ?? "GET") === "GET") return { status: 200, body: { holds: [HOLD] } };
      if ((url === "/api/staff/holds" || url === "/api/v1/staff/holds") && opts?.method === "PUT") return { status: 200, body: JSON.parse(opts.body as string) };
      return undefined;
    });
    render(<StaffPage />);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Holds" })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Holds" }));
    await waitFor(() => expect(screen.getByTestId("hold-h1")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Hold h1 text"), { target: { value: "Frozen for release" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, o]) => (o as RequestInit | undefined)?.method === "PUT");
      expect(put).toBeTruthy();
      const [, opts] = put as [string, RequestInit];
      expect((opts.headers as Record<string, string>)["X-Requested-With"]).toBe("XMLHttpRequest");
      expect(JSON.parse(opts.body as string)).toEqual({ holds: [{ ...HOLD, text: "Frozen for release" }] });
    });
  });
});

