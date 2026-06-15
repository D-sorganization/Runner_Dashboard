// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { OverviewPage } from "../OverviewPage";

vi.mock("../Events", () => ({
  OverviewEventSection: () => <div data-testid="overview-events" />,
}));

vi.mock("../OverviewLeases", () => ({
  OverviewLeases: () => <div data-testid="overview-leases" />,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function endpointPayload(url: string): unknown {
  switch (url) {
    case "/api/stats":
      return {
        queued: 2,
        in_progress: 1,
        org_open_prs: 5,
        org_open_issues: 8,
        runs_completed: 4,
        runs_success: 3,
        success_rate: 75,
      };
    case "/api/runners":
      return {
        runners: [
          {
            id: 1,
            name: "d-sorg-local-ControlTower-1",
            status: "online",
            busy: false,
            labels: ["self-hosted"],
          },
          {
            id: 2,
            name: "d-sorg-local-DeskComputer-1",
            status: "offline",
            busy: false,
            labels: [],
          },
        ],
      };
    case "/api/runs?per_page=30":
      return { runs: [] };
    case "/api/system":
      return { disk: { free_gb: 100 } };
    case "/api/queue":
      return { queued_count: 2, in_progress_count: 1 };
    case "/api/fleet/nodes":
      return {
        nodes: [
          {
            name: "ControlTower",
            online: true,
            dashboard_reachable: true,
            health: { runners_registered: 1 },
            system: {},
          },
        ],
      };
    case "/api/watchdog":
      return { status: "healthy", summary: "ok" };
    case "/api/deployment":
      return {
        git_branch: "main",
        git_sha: "abcdef1234567", // pragma: allowlist secret
        deployed_at: "2026-06-15T00:00:00Z",
      };
    case "/api/runner-routing-audit":
      return { violations: [] };
    case "/api/github/status":
      return { status: "ok" };
    case "/api/deployment/git-drift":
      return { is_drifted: false };
    default:
      throw new Error("unexpected GET " + url);
  }
}

function mockOverviewFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
    const url = String(input);
    if (url === "/api/fleet/control/all-up") {
      expect(init?.method).toBe("POST");
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    if (url === "/api/runners/1/stop") {
      expect(init?.method).toBe("POST");
      return Promise.resolve(jsonResponse({ ok: true }));
    }
    return Promise.resolve(jsonResponse(endpointPayload(url)));
  });
}

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="pathname">{loc.pathname}</span>;
}

function renderOverview() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <LocationProbe />
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/t/:tabId" element={<OverviewPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("OverviewPage", () => {
  it("loads the native overview data and composes overview-only sections", async () => {
    const fetchMock = mockOverviewFetch();

    renderOverview();

    expect(await screen.findByRole("region", { name: "Fleet status" })).toBeInTheDocument();
    expect(screen.getByTestId("overview-events")).toBeInTheDocument();
    expect(screen.getByTestId("overview-leases")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/runners",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("routes overview actions through the shell URL contract", async () => {
    mockOverviewFetch();

    renderOverview();

    await screen.findByRole("region", { name: "Fleet status" });
    fireEvent.click(screen.getByRole("button", { name: "Deployment state" }));

    expect(screen.getByTestId("pathname")).toHaveTextContent("/t/deployment");
  });

  it("dispatches fleet and runner actions through native handlers", async () => {
    const fetchMock = mockOverviewFetch();

    renderOverview();

    await screen.findByRole("region", { name: "Fleet status" });
    fireEvent.click(screen.getByRole("button", { name: /Start All/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/fleet/control/all-up",
        expect.objectContaining({ method: "POST" }),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: /^Stop$/ }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/runners/1/stop",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
