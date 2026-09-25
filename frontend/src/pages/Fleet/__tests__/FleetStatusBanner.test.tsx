// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FleetStatusBanner } from "../FleetStatusBanner";

afterEach(() => {
  cleanup();
});

describe("FleetStatusBanner", () => {
  it("never shows green/nominal before data has loaded (SC-A2 status honesty)", () => {
    render(
      <FleetStatusBanner
        loading={true}
        runnersLoaded={false}
        nodesLoaded={false}
        failedSources={[]}
        isStale={false}
        error={null}
        runners={[]}
        stats={{}}
      />
    );

    expect(screen.getByRole("region", { name: "Fleet status" })).toBeInTheDocument();
    expect(screen.getByText("Fleet Unknown")).toBeInTheDocument();
    expect(screen.getByText("Checking fleet…")).toBeInTheDocument();
    expect(screen.queryByText("All systems nominal")).not.toBeInTheDocument();
    expect(screen.queryByText("Fleet Operational")).not.toBeInTheDocument();
  });

  it("fails visibly and reports failed source when a source fails", () => {
    render(
      <FleetStatusBanner
        loading={false}
        runnersLoaded={false}
        nodesLoaded={true}
        failedSources={["/api/runners: HTTP 500"]}
        isStale={false}
        error="/api/runners: HTTP 500"
        runners={[]}
        stats={{}}
      />
    );

    expect(screen.getByText("Fleet Unknown")).toBeInTheDocument();
    expect(screen.getByText(/Fleet status unknown — \/api\/runners: HTTP 500/)).toBeInTheDocument();
    expect(screen.queryByText("All systems nominal")).not.toBeInTheDocument();
  });

  it("shows operational status when all sources loaded nominally", () => {
    render(
      <FleetStatusBanner
        loading={false}
        runnersLoaded={true}
        nodesLoaded={true}
        failedSources={[]}
        isStale={false}
        error={null}
        runners={[
          { id: 1, name: "d-sorg-local-ControlTower-1", status: "online", busy: false },
          { id: 2, name: "d-sorg-local-DeskComputer-1", status: "online", busy: true },
        ]}
        stats={{ runs_completed: 10, runs_success: 10, success_rate: 100 }}
      />
    );

    expect(screen.getByText("Fleet Operational")).toBeInTheDocument();
    expect(screen.getByText("All systems nominal")).toBeInTheDocument();
  });

  it("renders section jump links", () => {
    render(
      <FleetStatusBanner
        loading={false}
        runnersLoaded={true}
        nodesLoaded={true}
        failedSources={[]}
        isStale={false}
        error={null}
        runners={[]}
        stats={{}}
      />
    );

    expect(screen.getByRole("link", { name: /Machines/i })).toHaveAttribute("href", "#machines");
    expect(screen.getByRole("link", { name: /Runners/i })).toHaveAttribute("href", "#runners");
    expect(screen.getByRole("link", { name: /Alerts/i })).toHaveAttribute("href", "#alerts");
    expect(screen.getByRole("link", { name: /Event/i })).toHaveAttribute("href", "#events");
  });
});
