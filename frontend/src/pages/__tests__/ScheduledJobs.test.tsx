// @vitest-environment jsdom
/**
 * Tests for ScheduledJobs.tsx — decomposition #836 pass 2.
 *
 * Covers the extracted "Schedules" tab behaviour:
 * 1. Smoke render.
 * 2. Fetches /api/scheduled-workflows on mount and renders rows.
 * 3. Empty state when no scheduled workflows are returned.
 * 4. Text filter narrows the visible rows.
 * 5. Headline stats reflect the payload.
 * 6. Dry-run plan rendered when present.
 * 7. Refresh button re-fetches.
 */
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScheduledJobs from "../ScheduledJobs";

afterEach(cleanup);

const SAMPLE = {
  scheduled_workflow_count: 2,
  generated_at: new Date().toISOString(),
  repositories: [
    {
      repository: "D-sorg/alpha",
      scheduled_workflow_count: 2,
      workflows: [
        {
          workflow_name: "Nightly Build",
          workflow_path: ".github/workflows/nightly.yml",
          scheduled: true,
          enabled: true,
          cron_expressions: ["0 0 * * *"],
          latest_run: {
            conclusion: "success",
            created_at: new Date().toISOString(),
            html_url: "https://example.com/run/1",
          },
        },
        {
          workflow_name: "Jules Sweep",
          workflow_path: ".github/workflows/jules.yml",
          scheduled: true,
          enabled: false,
          cron_expressions: ["*/30 * * * *"],
          latest_run: null,
        },
      ],
    },
  ],
  dry_run_plan: {
    steps: [
      {
        action: "disable",
        // Distinct from the table-row workflow names so row assertions stay
        // unambiguous (the dry-run plan renders the same names otherwise).
        workflow_name: "Stale Nightly",
        repository: "D-sorg/alpha",
        reason: "redundant schedule",
      },
    ],
  },
};

/** Scope a text query to the scheduled-workflows table body (excludes the
 * dry-run plan table, which can repeat workflow names). */
function tableRow(name: string): HTMLElement {
  const tables = document.querySelectorAll("table");
  const rowsTable = tables[0] as HTMLElement;
  return within(rowsTable).getByText(name).closest("tr") as HTMLElement;
}

function mockFetch(payload: unknown) {
  return vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(payload),
    } as Response),
  );
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

describe("ScheduledJobs", () => {
  it("renders without throwing (smoke test)", () => {
    global.fetch = mockFetch({ repositories: [] });
    expect(() => render(<ScheduledJobs />)).not.toThrow();
  });

  it("fetches scheduled workflows on mount and renders rows", async () => {
    const fetchMock = mockFetch(SAMPLE);
    global.fetch = fetchMock;
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(within(document.querySelectorAll("table")[0] as HTMLElement).getByText("Nightly Build")).toBeInTheDocument();
    });
    expect(tableRow("Jules Sweep")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/scheduled-workflows",
      expect.anything(),
    );
  });

  it("shows the empty state when there are no scheduled workflows", async () => {
    global.fetch = mockFetch({ repositories: [], scheduled_workflow_count: 0 });
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(screen.getByText(/No scheduled workflows found/i)).toBeInTheDocument();
    });
    expect(screen.getByTestId("scheduled-jobs-empty")).toHaveClass("empty-state");
  });

  it("filters rows by the text filter", async () => {
    global.fetch = mockFetch(SAMPLE);
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(screen.getByText("Nightly Build")).toBeInTheDocument();
    });
    const filter = screen.getByLabelText(/Filter scheduled workflows by name or repo/i);
    fireEvent.change(filter, { target: { value: "nightly" } });
    expect(screen.getByText("Nightly Build")).toBeInTheDocument();
    expect(screen.queryByText("Jules Sweep")).not.toBeInTheDocument();
  });

  it("shows the no-match empty state when the filter matches nothing", async () => {
    global.fetch = mockFetch(SAMPLE);
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(screen.getByText("Nightly Build")).toBeInTheDocument();
    });
    const filter = screen.getByLabelText(/Filter scheduled workflows by name or repo/i);
    fireEvent.change(filter, { target: { value: "zzz-no-match" } });
    expect(screen.getByText(/No workflows match the current filter/i)).toBeInTheDocument();
    expect(screen.getByTestId("scheduled-jobs-empty")).toHaveClass("empty-state");
  });

  it("renders headline stats from the payload", async () => {
    global.fetch = mockFetch(SAMPLE);
    render(<ScheduledJobs />);
    // Jules + Disabled stats appear because the sample has one of each. Scope
    // to the stat-row: "Disabled" also matches the "disabled" status pill.
    const statRow = document.querySelector(".stat-row") as HTMLElement;
    await waitFor(() => {
      expect(within(statRow).getByText(/Jules Schedules/i)).toBeInTheDocument();
    });
    expect(within(statRow).getByText(/Disabled/i)).toBeInTheDocument();
  });

  it("renders the dry-run plan when present", async () => {
    global.fetch = mockFetch(SAMPLE);
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(screen.getByText(/Dry-Run Plan/i)).toBeInTheDocument();
    });
    expect(screen.getByText("redundant schedule")).toBeInTheDocument();
  });

  it("re-fetches when Refresh is clicked", async () => {
    const fetchMock = mockFetch(SAMPLE);
    global.fetch = fetchMock;
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(screen.getByText("Nightly Build")).toBeInTheDocument();
    });
    const before = fetchMock.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /Refresh scheduled workflows/i }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("marks disabled workflows with a disabled status pill", async () => {
    global.fetch = mockFetch(SAMPLE);
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(tableRow("Jules Sweep")).toBeInTheDocument();
    });
    const disabled = within(tableRow("Jules Sweep")).getByText("disabled");
    expect(disabled).toHaveAttribute("data-touch-primitive", "Badge");
    expect(disabled).toHaveClass("badge-tone-neutral");
  });

  it("renders latest-run conclusions through the Badge primitive", async () => {
    global.fetch = mockFetch(SAMPLE);
    render(<ScheduledJobs />);
    await waitFor(() => {
      expect(tableRow("Nightly Build")).toBeInTheDocument();
    });
    const success = Array.from(within(tableRow("Nightly Build")).getAllByText("success")).find(
      (node) => node.getAttribute("data-touch-primitive") === "Badge",
    );
    expect(success).toBeDefined();
    expect(success).toHaveAttribute("data-touch-primitive", "Badge");
    expect(success).toHaveClass("badge-tone-success");
  });

  it("uses the Skeleton primitive while the first fetch is pending", () => {
    global.fetch = vi.fn(() => new Promise<Response>(() => {}));
    render(<ScheduledJobs />);
    expect(screen.getByRole("status", { name: /Loading scheduled workflows/i })).toBeInTheDocument();
    expect(document.querySelector(".skeleton-card")).toBeInTheDocument();
  });
});
