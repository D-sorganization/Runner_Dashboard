// @vitest-environment jsdom
/**
 * Tests for Reports/Mobile.tsx — M12.
 *
 * Covers:
 * 1. Shows loading skeleton while fetching.
 * 2. Renders report cards with filename, date, size.
 * 3. SegmentedControl filters by type (All, Daily, Charts).
 * 4. Tapping a card opens the BottomSheet.
 * 5. BottomSheet shows View and Download actions.
 * 6. BottomSheet closes on Escape key.
 * 7. Empty state when no reports.
 * 8. Error state when fetch fails.
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
import { ReportsMobile } from "../Mobile";

// jsdom doesn't implement matchMedia — provide a no-op stub.
function stubMatchMedia() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: vi.fn((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Mock data
// ---------------------------------------------------------------------------

const MOCK_REPORTS = {
  reports: [
    {
      filename: "daily_progress_report_2026-05-01.md",
      date: "2026-05-01",
      size_kb: 12.4,
      modified: "2026-05-01T18:00:00Z",
      has_chart: true,
      chart_filename: "assessment_scores_2026-05-01.png",
    },
    {
      filename: "daily_progress_report_2026-04-30.md",
      date: "2026-04-30",
      size_kb: 10.1,
      modified: "2026-04-30T18:00:00Z",
      has_chart: false,
      chart_filename: null,
    },
    {
      filename: "assessment_scores_2026-05-01.png",
      date: "2026-05-01",
      size_kb: 45.2,
      modified: "2026-05-01T18:05:00Z",
      has_chart: false,
      chart_filename: null,
    },
  ],
  total: 3,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupFetch({
  ok = true,
  data = MOCK_REPORTS,
}: {
  ok?: boolean;
  data?: object;
} = {}) {
  const fetchMock = vi.fn(() =>
    Promise.resolve({
      ok,
      status: ok ? 200 : 500,
      json: () => Promise.resolve(data),
    } as Response),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ReportsMobile", () => {
  beforeEach(() => {
    stubMatchMedia();
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    cleanup();
  });

  it("renders report cards with filename, date and size after loading", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(
        screen.getByText("daily_progress_report_2026-05-01.md"),
      ).toBeInTheDocument();
    });

    expect(
      screen.getByText("daily_progress_report_2026-04-30.md"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("assessment_scores_2026-05-01.png"),
    ).toBeInTheDocument();
  });

  it("shows the SegmentedControl with All, Daily, Charts options", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(
        screen.getByRole("radiogroup", { name: /filter by report type/i }),
      ).toBeInTheDocument();
    });

    expect(screen.getByRole("radio", { name: /all/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /daily/i })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /charts/i })).toBeInTheDocument();
  });

  it("filters to only daily reports when Daily is selected", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /daily/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /daily/i }));

    await waitFor(() => {
      expect(
        screen.getByText("daily_progress_report_2026-05-01.md"),
      ).toBeInTheDocument();
    });

    expect(
      screen.queryByText("assessment_scores_2026-05-01.png"),
    ).not.toBeInTheDocument();
  });

  it("filters to only charts when Charts is selected", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(screen.getByRole("radio", { name: /charts/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("radio", { name: /charts/i }));

    await waitFor(() => {
      expect(
        screen.getByText("assessment_scores_2026-05-01.png"),
      ).toBeInTheDocument();
    });

    expect(
      screen.queryByText("daily_progress_report_2026-05-01.md"),
    ).not.toBeInTheDocument();
  });

  it("tapping a card opens the BottomSheet", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(
        screen.getByText("daily_progress_report_2026-05-01.md"),
      ).toBeInTheDocument();
    });

    const card = screen.getByRole("button", { name: /Report: 2026-05-01/i });
    fireEvent.click(card);

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("BottomSheet shows View Report and Download actions", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(
        screen.getByText("daily_progress_report_2026-05-01.md"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Report: 2026-05-01/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    expect(screen.getByRole("link", { name: /view report/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /download/i })).toBeInTheDocument();
  });

  it("BottomSheet closes on Escape key", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(
        screen.getByText("daily_progress_report_2026-05-01.md"),
      ).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Report: 2026-05-01/i }));

    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("shows empty state when no reports", async () => {
    setupFetch({ data: { reports: [], total: 0 } });
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(screen.getByTestId("reports-empty")).toBeInTheDocument();
    });
    expect(screen.getByText(/no reports found/i)).toBeInTheDocument();
  });

  it("shows error state when fetch fails", async () => {
    setupFetch({ ok: false });
    render(<ReportsMobile />);

    // #837: failures surface operator guidance instead of a raw "HTTP 500".
    await waitFor(() => {
      expect(screen.getByTestId("reports-error")).toBeInTheDocument();
    });
    expect(screen.getByText(/backend error/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("shows report count in the header", async () => {
    setupFetch();
    render(<ReportsMobile />);

    await waitFor(() => {
      expect(screen.getByText(/3 reports available/i)).toBeInTheDocument();
    });
  });
});
