// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

const mockFetchSessions = vi.fn();

vi.mock("../fleetApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../fleetApi")>();
  return {
    ...actual,
    fetchSessions: (...args: Parameters<typeof actual.fetchSessions>) => mockFetchSessions(...args),
  };
});

import { ActiveWorkPanel } from "../ActiveWorkPanel";

afterEach(() => {
  cleanup();
  mockFetchSessions.mockReset();
});

describe("ActiveWorkPanel completeness badges", () => {
  it("renders 'partial staff read' when complete is true and staff_complete is false", async () => {
    mockFetchSessions.mockResolvedValue({
      available: true,
      complete: true,
      staff_complete: false,
      sessions: [],
      staff_runs: [],
      warnings: ["Peer Slow timed out after 3.0s"],
    });

    render(<ActiveWorkPanel />);
    expect(await screen.findByText("partial staff read")).toBeInTheDocument();
    expect(screen.queryByText("partial board read")).toBeNull();
  });

  it("renders 'partial board read' when complete is false", async () => {
    mockFetchSessions.mockResolvedValue({
      available: true,
      complete: false,
      staff_complete: true,
      sessions: [],
      staff_runs: [],
      warnings: ["board sessions read timed out"],
    });

    render(<ActiveWorkPanel />);
    expect(await screen.findByText("partial board read")).toBeInTheDocument();
    expect(screen.queryByText("partial staff read")).toBeNull();
  });

  it("omits partial-read badges when both complete and staff_complete are true", async () => {
    mockFetchSessions.mockResolvedValue({
      available: true,
      complete: true,
      staff_complete: true,
      sessions: [],
      staff_runs: [],
      warnings: [],
    });

    render(<ActiveWorkPanel />);
    await screen.findByTestId("active-work-empty");
    expect(screen.queryByText("partial staff read")).toBeNull();
    expect(screen.queryByText("partial board read")).toBeNull();
  });
});
