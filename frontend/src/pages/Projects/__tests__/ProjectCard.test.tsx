// @vitest-environment jsdom
/**
 * ProjectCard header (#1718): at 1280px a long repo name pushed "Run steward
 * now" past the card edge, where `overflow: hidden` clipped it.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ProjectCard } from "../ProjectCard";
import type { ProjectOverview } from "../types";

afterEach(cleanup);

const PROJECT: ProjectOverview = {
  repo: "Gasification_Model",
  charter_present: false,
  features: [],
  progress: { planned: 0, in_progress: 0, shipped: 0, parked: 0, percent_shipped: 0 },
  status_present: false,
  decisions_needed: [],
  last_steward_run: null,
};

describe("ProjectCard header", () => {
  it("wraps instead of pushing the steward button out of the card (#1718)", () => {
    render(
      <ProjectCard
        project={PROJECT}
        running={false}
        onRunSteward={() => undefined}
        onRequestAssessment={() => undefined}
      />,
    );
    const button = screen.getByRole("button", { name: "Run steward now for Gasification_Model" });
    const header = button.parentElement as HTMLElement;
    expect(header).toHaveStyle({ flexWrap: "wrap" });
    expect(button).toHaveStyle({ flexShrink: "0" });
  });

  it("keeps long decision lists collapsed while exposing their count and every decision", () => {
    const project = {
      ...PROJECT,
      decisions_needed: ["Choose the [runtime](https://example.com/runtime)", "Set a rollout date"],
    };
    render(
      <ProjectCard
        project={project}
        running={false}
        onRunSteward={() => undefined}
        onRequestAssessment={() => undefined}
      />,
    );

    const disclosure = screen.getByText("View 2 decisions").closest("details");
    expect(disclosure).not.toHaveAttribute("open");
    expect(screen.getByRole("link", { name: "runtime" })).toHaveAttribute("href", "https://example.com/runtime");
    fireEvent.click(screen.getByText("View 2 decisions"));
    expect(disclosure).toHaveAttribute("open");
    expect(screen.getByRole("link", { name: "runtime" })).toHaveAttribute("href", "https://example.com/runtime");
    expect(screen.getByText("Set a rollout date")).toBeInTheDocument();
  });

  it("labels empty decision state without a disclosure", () => {
    render(
      <ProjectCard
        project={PROJECT}
        running={false}
        onRunSteward={() => undefined}
        onRequestAssessment={() => undefined}
      />,
    );
    expect(screen.getByText("Decisions needed").parentElement).toHaveTextContent("none");
    expect(screen.queryByText(/View \\d+ decisions/)).not.toBeInTheDocument();
  });
});
