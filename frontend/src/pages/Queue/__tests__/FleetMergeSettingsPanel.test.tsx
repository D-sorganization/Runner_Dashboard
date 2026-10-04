// @vitest-environment jsdom
import React from "react";
import { describe, it, expect, afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FleetMergeSettingsPanel } from "../FleetMergeSettingsPanel";

describe("FleetMergeSettingsPanel", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders collapsed header and Check button", () => {
    render(<FleetMergeSettingsPanel />);
    expect(screen.getByText(/Fleet Merge Settings/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Check Fleet Settings/i })).toBeInTheDocument();
  });

  it("fetches merge settings on button click and renders green results", async () => {
    const mockPayload = {
      status: "pass",
      results: [
        {
          repo: "D-sorganization/Runner_Dashboard",
          status: "pass",
          findings: [],
        },
        {
          repo: "D-sorganization/UpstreamDrift",
          status: "pass",
          findings: [],
        },
      ],
    };

    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(mockPayload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<FleetMergeSettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Check Fleet Settings/i }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/queue/merge-settings");
      expect(screen.getByText("D-sorganization/Runner_Dashboard")).toBeInTheDocument();
      expect(screen.getByText("D-sorganization/UpstreamDrift")).toBeInTheDocument();
      expect(screen.getAllByText("PASS").length).toBe(2);
      expect(screen.getByText("All Green")).toBeInTheDocument();
    });
  });

  it("renders drift findings when status is fail", async () => {
    const mockPayload = {
      status: "fail",
      results: [
        {
          repo: "D-sorganization/AffineDrift",
          status: "fail",
          findings: ["strict status check enabled, expected disabled"],
        },
      ],
    };

    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(mockPayload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<FleetMergeSettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Check Fleet Settings/i }));

    await waitFor(() => {
      expect(screen.getByText("D-sorganization/AffineDrift")).toBeInTheDocument();
      expect(screen.getByText("DRIFT")).toBeInTheDocument();
      expect(screen.getByText(/strict status check enabled/i)).toBeInTheDocument();
      expect(screen.getByText("Drift Detected")).toBeInTheDocument();
    });
  });

  it("handles fetch failure gracefully", async () => {
    const fetchMock = vi.fn(() => Promise.reject(new Error("Network connection failed")));
    vi.stubGlobal("fetch", fetchMock);

    render(<FleetMergeSettingsPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Check Fleet Settings/i }));

    await waitFor(() => {
      expect(screen.getByText(/Failed to load fleet merge settings/i)).toBeInTheDocument();
      expect(screen.getByText("Error")).toBeInTheDocument();
    });
  });
});
