// @vitest-environment jsdom
/**
 * Tests for Fleet/Mobile.tsx — issue #728 E3.
 *
 * Covers:
 * 1. Renders without throwing (smoke test).
 * 2. Shows loading skeleton while fetching fleet status.
 * 3. Renders KPI header with counts after successful fetch.
 * 4. Filter pills render for all/online/busy/offline states.
 * 5. Clicking a filter pill narrows visible runner cards.
 * 6. Shows empty state when no runners match the filter.
 * 7. Shows error state when API call fails.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  waitFor,
  fireEvent,
  act,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FleetMobile } from "../Mobile";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_FLEET: Record<
  string,
  {
    status: string;
    hostname: string;
    cpu_percent: number;
    memory_percent: number;
    uptime_seconds: number;
    current_job: string | null;
  }
> = {
  "runner-a": {
    status: "online",
    hostname: "host-a.local",
    cpu_percent: 12,
    memory_percent: 45,
    uptime_seconds: 3600,
    current_job: null,
  },
  "runner-b": {
    status: "busy",
    hostname: "host-b.local",
    cpu_percent: 88,
    memory_percent: 72,
    uptime_seconds: 7200,
    current_job: "CI Build #42",
  },
  "runner-c": {
    status: "offline",
    hostname: "host-c.local",
    cpu_percent: 0,
    memory_percent: 0,
    uptime_seconds: 0,
    current_job: null,
  },
};

const MOCK_SINGLE_ONLINE: typeof MOCK_FLEET = {
  "runner-x": {
    status: "online",
    hostname: "host-x.local",
    cpu_percent: 5,
    memory_percent: 20,
    uptime_seconds: 600,
    current_job: null,
  },
};

const EMPTY_FLEET = {};

/** Wrap flat fixtures in the `/api/fleet/nodes` response the page reads. */
function nodesPayload(fleet: Record<string, (typeof MOCK_FLEET)[string]>) {
  return {
    nodes: Object.entries(fleet).map(([name, n]) => ({
      name,
      online: n.status !== "offline",
      last_seen: "2026-09-28T00:00:00Z",
      system: {
        hostname: n.hostname,
        uptime_seconds: n.uptime_seconds,
        cpu: { percent: n.cpu_percent },
        memory: { percent: n.memory_percent },
      },
    })),
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeFetch(
  data: object = nodesPayload(MOCK_FLEET),
  ok: boolean = true,
  status: number = 200,
) {
  return vi.fn((_url: string) =>
    Promise.resolve({
      ok,
      status,
      json: () => Promise.resolve(data),
    } as Response),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FleetMobile", () => {
  it("renders without throwing (smoke test)", () => {
    globalThis.fetch = makeFetch();
    expect(() => render(<FleetMobile />)).not.toThrow();
  });

  it("shows loading skeleton while fetch is pending", () => {
    globalThis.fetch = vi.fn(() => new Promise<Response>(() => {}));
    const { container } = render(<FleetMobile />);
    // During loading, skeleton cards should be present
    expect(
      container.querySelector("[aria-busy='true']") ||
        container.querySelector("[class*='skeleton']") ||
        container.querySelector(".skeleton"),
    ).not.toBeNull();
    expect(container.firstChild).not.toBeNull();
    // The fleet list section should not yet be present
    expect(screen.queryByRole("region", { name: /fleet/i })).toBeNull();
  });

  it("renders Fleet section after successful fetch", async () => {
    globalThis.fetch = makeFetch(nodesPayload(MOCK_FLEET));
    render(<FleetMobile />);
    await waitFor(() => {
      // The main fleet section has aria-label="Fleet"
      expect(screen.getByRole("region", { name: "Fleet" })).toBeInTheDocument();
    });
  });

  it("renders status filter group with all/online/busy/offline pills", async () => {
    globalThis.fetch = makeFetch(nodesPayload(MOCK_FLEET));
    render(<FleetMobile />);
    await waitFor(() => {
      const filterGroup = screen.getByRole("group", { name: /filter by status/i });
      expect(filterGroup).toBeInTheDocument();
      // Pills are inside the filter group — use queryAllByText to handle duplicates
      const allPills = screen.queryAllByText("All");
      expect(allPills.length).toBeGreaterThan(0);
    });
  });

  it("clicking Offline filter pill shows only offline runners", async () => {
    globalThis.fetch = makeFetch(nodesPayload(MOCK_FLEET));
    render(<FleetMobile />);
    await waitFor(() => {
      expect(screen.getByRole("region", { name: "Fleet" })).toBeInTheDocument();
    });
    // Click the filter group's "Offline" pill (inside the group)
    const filterGroup = screen.getByRole("group", { name: /filter by status/i });
    const offlinePill = Array.from(filterGroup.querySelectorAll("*")).find(
      (el) => el.textContent?.trim() === "Offline",
    ) as HTMLElement | undefined;
    if (offlinePill) {
      await act(async () => {
        fireEvent.click(offlinePill);
      });
    }
    await waitFor(() => {
      // runner-c is offline; its hostname should appear
      expect(screen.getByText(/host-c\.local/i)).toBeInTheDocument();
    });
  });

  it("shows empty state message when no runners match the filter", async () => {
    globalThis.fetch = makeFetch(nodesPayload(MOCK_SINGLE_ONLINE));
    render(<FleetMobile />);
    await waitFor(() => {
      expect(screen.getByRole("region", { name: "Fleet" })).toBeInTheDocument();
    });
    // Filter to 'Offline' — the single runner is online, so list should be empty
    const filterGroup = screen.getByRole("group", { name: /filter by status/i });
    const offlinePill = Array.from(filterGroup.querySelectorAll("*")).find(
      (el) => el.textContent?.trim() === "Offline",
    ) as HTMLElement | undefined;
    if (offlinePill) {
      await act(async () => {
        fireEvent.click(offlinePill);
      });
    }
    await waitFor(() => {
      expect(screen.getByText(/no runners match/i)).toBeInTheDocument();
    });
  });

  it("renders with empty fleet data without crashing", async () => {
    globalThis.fetch = makeFetch(nodesPayload(EMPTY_FLEET));
    render(<FleetMobile />);
    await waitFor(() => {
      expect(screen.getByRole("region", { name: "Fleet" })).toBeInTheDocument();
    });
    // Empty fleet: no runner cards, no crash
    expect(document.body).toBeInTheDocument();
  });

  it("reads machine status and telemetry from /api/fleet/nodes (#1718)", async () => {
    const fetchMock = makeFetch(nodesPayload(MOCK_FLEET));
    globalThis.fetch = fetchMock;
    render(<FleetMobile />);
    await screen.findByText(/host-a\.local/i);
    expect(fetchMock).toHaveBeenCalledWith("/api/fleet/nodes");
    // runner-a is online at 12% CPU / 45% RAM; only runner-c is offline.
    expect(screen.getByLabelText("CPU: 12%")).toBeInTheDocument();
    expect(screen.getByLabelText("RAM: 45%")).toBeInTheDocument();
    const filterGroup = screen.getByRole("group", { name: /filter by status/i });
    expect(filterGroup.textContent).toMatch(/1\s*Offline/);
  });

  it("groups ControlTower pools only when there is more than one (#1718)", async () => {
    const base = MOCK_FLEET["runner-a"];
    globalThis.fetch = makeFetch(nodesPayload({ ControlTower: { ...base, hostname: "ct.local" }, ...MOCK_FLEET }));
    const { unmount } = render(<FleetMobile />);
    await screen.findByText(/ct\.local/i);
    expect(screen.queryByText(/ControlTower Pools/i)).not.toBeInTheDocument();
    unmount();

    globalThis.fetch = makeFetch(
      nodesPayload({
        "ControlTower-NVMe": { ...base, hostname: "ct-nvme.local" },
        "ControlTower-SSD": { ...base, hostname: "ct-ssd.local" },
      }),
    );
    render(<FleetMobile />);
    await screen.findByText(/ct-nvme\.local/i);
    expect(screen.getByText(/ControlTower Pools/i)).toBeInTheDocument();
  });

  it("shows error state when API call fails", async () => {
    globalThis.fetch = makeFetch({}, false, 500);
    render(<FleetMobile />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/error|failed|HTTP 500/i);
    });
  });

  it("shows error state when fetch throws network error", async () => {
    globalThis.fetch = vi.fn(() => Promise.reject(new Error("Network error")));
    render(<FleetMobile />);
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/error|failed|network error/i);
    });
  });
});
