// @vitest-environment jsdom
/**
 * Behaviour tests for pages/FleetTab.tsx — the full "Fleet / Overview" tab
 * extracted from the legacy App.tsx monolith (decomposition #836, pass 12).
 *
 * Covers, against the legacy behaviour this 1:1 port preserves:
 * 1. The fleet-status hero panel summarises machine/PR/queue/runner KPIs and
 *    surfaces computed alerts; KPI buttons route via setTab.
 * 2. The KPI stat row reflects runners-online / machines-online / queue counts.
 * 3. The deployment build note renders the short SHA and the "Deployment
 *    state" button fires onOpenDeployment.
 * 4. The drift banner appears only when /api/deployment/git-drift reports drift.
 * 5. The Machine Health table lists each machine with online/offline badges.
 * 6. The Runner Fleet status filters narrow the visible runner rows.
 * 7. Fleet-wide controls thread the correct action through onFleet, and a
 *    per-runner Start/Stop button threads the runner id + action through onRunner.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetTab } from "../FleetTab";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  // Default: drift endpoint reports no drift.
  stubFetch({ is_drifted: false });
});

function stubFetch(driftPayload: Record<string, unknown>) {
  const fetchFn = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(driftPayload),
    } as unknown as Response),
  );
  vi.stubGlobal("fetch", fetchFn);
  return fetchFn;
}

const RUNNERS = [
  { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: false, labels: ["self-hosted", "gpu"] },
  { id: 2, name: "d-sorg-local-ControlTower-2", status: "online", busy: true, labels: [] },
  { id: 3, name: "d-sorg-local-DeskComputer-1", status: "offline", busy: false, labels: [] },
];

const MACHINES_DATA = {
  nodes: [
    {
      name: "ControlTower",
      online: true,
      dashboard_reachable: true,
      last_seen: "2026-06-02T00:00:00Z",
      system: { cpu: { percent_1m_avg: 20 }, memory: { total_gb: 32, available_gb: 16 }, uptime_seconds: 3600 },
      health: { runners_registered: 2 },
    },
  ],
};

const BASE_PROPS = {
  runners: RUNNERS,
  stats: {
    queued: 4,
    in_progress: 1,
    org_open_prs: 7,
    org_open_issues: 12,
    runs_completed: 10,
    runs_success: 9,
    success_rate: 90,
  },
  watchdog: { status: "healthy", summary: "keepalive ok" },
  queue: {},
  machinesData: MACHINES_DATA,
  deployment: { git_branch: "main", git_sha: "abcdef1234567", deployed_at: "2026-06-01" }, // pragma: allowlist secret
  system: { disk: { free_gb: 200, percent: 40, path: "/", pressure: { status: "healthy" } } },
  runs: [],
  runnerAudit: { violations: [] },
  loading: false,
};

function renderFleet(overrides: Record<string, unknown> = {}) {
  return render(<FleetTab {...BASE_PROPS} {...overrides} />);
}

describe("FleetTab hero + KPIs", () => {
  it("renders the fleet-status hero with KPI values", () => {
    renderFleet();
    const hero = screen.getByRole("region", { name: "Fleet status" });
    expect(hero).toBeInTheDocument();
    // Machines 1/2 (ControlTower online, DeskComputer synthesized offline),
    // Open PRs 7, Runners 2/3.
    expect(within(hero).getByText("1 / 2")).toBeInTheDocument();
    expect(within(hero).getByText("7")).toBeInTheDocument();
    expect(within(hero).getByText("2 / 3")).toBeInTheDocument();
  });

  it("routes via setTab when a hero KPI button is clicked", () => {
    const setTab = vi.fn();
    renderFleet({ setTab });
    const hero = screen.getByRole("region", { name: "Fleet status" });
    fireEvent.click(within(hero).getByText("Machines").closest("button")!);
    expect(setTab).toHaveBeenCalledWith("machines");
    fireEvent.click(within(hero).getByText("Queue").closest("button")!);
    expect(setTab).toHaveBeenCalledWith("queue");
  });

  it("reflects the runners-online / open-issues stat cards", () => {
    renderFleet();
    expect(screen.getByText("Runners Online")).toBeInTheDocument();
    expect(screen.getByText("2/3")).toBeInTheDocument();
    expect(screen.getByText("Open Issues")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });
});

describe("FleetTab deployment note + drift banner", () => {
  it("renders the short SHA and fires onOpenDeployment", () => {
    const onOpenDeployment = vi.fn();
    renderFleet({ onOpenDeployment });
    expect(screen.getByText("main@abcdef1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Deployment state" }));
    expect(onOpenDeployment).toHaveBeenCalledTimes(1);
  });

  it("shows the drift banner only when the endpoint reports drift", async () => {
    stubFetch({ is_drifted: true, source_commit: "aaa", remote_commit: "bbb" });
    renderFleet();
    await waitFor(() =>
      expect(
        screen.getByText(/Deployed version is behind origin\/main/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/local: aaa → remote: bbb/)).toBeInTheDocument();
  });

  it("does not show the drift banner when there is no drift", async () => {
    renderFleet();
    // Allow the mount fetch to settle.
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(
      screen.queryByText(/Deployed version is behind origin\/main/),
    ).not.toBeInTheDocument();
  });
});

describe("FleetTab machine health table", () => {
  it("lists each machine with an online badge", () => {
    renderFleet();
    expect(screen.getByText("Machine Health")).toBeInTheDocument();
    // ControlTower appears as a row strong label.
    const controlTowerCells = screen.getAllByText("ControlTower");
    expect(controlTowerCells.length).toBeGreaterThan(0);
    expect(screen.getAllByText("online").length).toBeGreaterThan(0);
  });
});

describe("FleetTab runner fleet controls", () => {
  it("threads fleet-wide actions through onFleet", () => {
    const onFleet = vi.fn();
    renderFleet({ onFleet });
    fireEvent.click(screen.getByRole("button", { name: /Start All/ }));
    expect(onFleet).toHaveBeenCalledWith("all-up");
    fireEvent.click(screen.getByRole("button", { name: /Stop All/ }));
    expect(onFleet).toHaveBeenCalledWith("all-down");
    fireEvent.click(screen.getByRole("button", { name: /Scale Up/ }));
    expect(onFleet).toHaveBeenCalledWith("up");
    fireEvent.click(screen.getByRole("button", { name: /Scale Down/ }));
    expect(onFleet).toHaveBeenCalledWith("down");
  });

  it("threads a per-runner action through onRunner", () => {
    const onRunner = vi.fn();
    renderFleet({ onRunner });
    // The offline DeskComputer runner shows a "Start" button.
    const startButtons = screen.getAllByRole("button", { name: "Start" });
    fireEvent.click(startButtons[0]);
    expect(onRunner).toHaveBeenCalledWith(3, "start");
  });

  it("narrows visible runners when the Offline status filter is selected", () => {
    renderFleet();
    // The status-filter group exposes pills; click the "Offline" filter.
    const filterGroup = screen.getByRole("group", { name: "Runner status filters" });
    fireEvent.click(within(filterGroup).getByText("Offline").closest("button")!);
    // Only the offline DeskComputer runner remains in the desktop table; the
    // online ControlTower runners are filtered out of the per-machine tables.
    // (The mobile card list always renders all filtered runners too, so the
    // offline name appears at least once.)
    expect(
      screen.getAllByText("d-sorg-local-DeskComputer-1").length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByText("d-sorg-local-ControlTower-1"),
    ).not.toBeInTheDocument();
  });
});
