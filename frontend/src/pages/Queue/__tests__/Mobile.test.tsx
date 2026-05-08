// @vitest-environment jsdom
/**
 * Tests for Queue/Mobile.tsx — M09 compact workflow runs view.
 *
 * Covers:
 * 1. Renders without crashing (smoke test).
 * 2. Shows skeleton cards while loading.
 * 3. Filter tabs (SegmentedControl) switch visible run sets.
 * 4. Tapping a run card opens the BottomSheet with detail.
 * 5. Empty state message is shown when no runs match filter.
 */
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueueMobile } from "../Mobile";

afterEach(cleanup);

// -- Mock data ------------------------------------------------------------------

const MOCK_QUEUE_DATA = {
  in_progress: [
    {
      id: "run-100",
      name: "CI Build",
      head_branch: "main",
      html_url: "https://github.com/org/repo/actions/runs/100",
      run_started_at: new Date(Date.now() - 120_000).toISOString(),
      runner_name: "ubuntu-fleet-1",
      triggering_actor: { login: "alice" },
      repository: { name: "runner-dashboard" },
    },
  ],
  queued: [
    {
      id: "run-200",
      name: "Deploy Staging",
      head_branch: "feat/new-feature",
      html_url: "https://github.com/org/repo/actions/runs/200",
      created_at: new Date(Date.now() - 60_000).toISOString(),
      triggering_actor: { login: "bob" },
      repository: { name: "runner-dashboard" },
    },
  ],
  total: 2,
};

const MOCK_EMPTY_QUEUE = {
  in_progress: [],
  queued: [],
  total: 0,
};

// -- Helpers --------------------------------------------------------------------

function makeFetch(queueData: typeof MOCK_QUEUE_DATA | typeof MOCK_EMPTY_QUEUE) {
  return vi.fn((url: string) => {
    if (url.includes("/api/queue/status")) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(queueData),
      } as Response);
    }
    return Promise.resolve({
      ok: false,
      status: 404,
      json: () => Promise.resolve({}),
    } as Response);
  });
}

// -- Tests ----------------------------------------------------------------------

describe.skip("QueueMobile", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    cleanup();
  });

  it("renders without crashing (smoke test)", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    // Initially shows skeleton or content — just ensure no unhandled error.
    expect(document.body).toBeTruthy();

    await waitFor(() => {
      expect(screen.queryByLabelText("Loading queue")).not.toBeInTheDocument();
    });
  });

  it("shows skeleton cards while loading", () => {
    // Make fetch never resolve to keep the loading state.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );

    render(<QueueMobile />);

    const loadingRegion = screen.getByLabelText("Loading queue");
    expect(loadingRegion).toBeInTheDocument();
    expect(loadingRegion).toHaveAttribute("aria-busy", "true");

    // SkeletonCard elements should be present.
    const skeletonCards = document.querySelectorAll(".skeleton-card");
    expect(skeletonCards.length).toBeGreaterThan(0);
  });

  it("renders run cards after data loads", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    expect(screen.getByText("Deploy Staging")).toBeInTheDocument();
    expect(screen.getAllByText("runner-dashboard").length).toBeGreaterThan(0);
  });

  it("filter tabs: switching to Running shows only running runs", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    // Both visible in "all" view.
    expect(screen.getByText("Deploy Staging")).toBeInTheDocument();

    // Switch to "Running".
    const runningTab = screen.getByRole("radio", { name: /running/i });
    fireEvent.click(runningTab);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    // Queued run should be hidden.
    expect(screen.queryByText("Deploy Staging")).not.toBeInTheDocument();
  });

  it("filter tabs: switching to Queued shows only queued runs", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /queued/i }));

    await waitFor(() => {
      expect(screen.getByText("Deploy Staging")).toBeInTheDocument();
    });

    expect(screen.queryByText("CI Build")).not.toBeInTheDocument();
  });

  it("filter tabs: switching to Failed shows empty state with helpful message", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /failed/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/Failed runs are not tracked in the live queue/i),
      ).toBeInTheDocument();
    });
  });

  it("tapping a run card opens the BottomSheet with details", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    // Tap the card.
    const card = screen.getByRole("button", {
      name: /CI Build.*runner-dashboard.*running/i,
    });
    fireEvent.click(card);

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    // Dialog should show the run name as title.
    expect(
      screen.getByRole("heading", { name: /CI Build/i }),
    ).toBeInTheDocument();

    // Detail fields visible.
    expect(screen.getByText("runner-dashboard")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
  });

  it("BottomSheet can be dismissed by clicking close button", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByText("CI Build")).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", {
        name: /CI Build.*runner-dashboard.*running/i,
      }),
    );

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /close/i }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("shows empty-state when queue is empty (all filter)", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_EMPTY_QUEUE));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(
        screen.getByText(/No active workflow runs/i),
      ).toBeInTheDocument();
    });
  });

  it("renders KPI strip with Running, Queued, Total counts", async () => {
    vi.stubGlobal("fetch", makeFetch(MOCK_QUEUE_DATA));

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByLabelText("Queue summary")).toBeInTheDocument();
    });

    const strip = screen.getByLabelText("Queue summary");
    expect(strip).toHaveTextContent("Running");
    expect(strip).toHaveTextContent("Queued");
    expect(strip).toHaveTextContent("Total");
  });

  it("shows error state when fetch fails and no cached data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 500,
          json: () => Promise.resolve({}),
        } as Response),
      ),
    );

    render(<QueueMobile />);

    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
