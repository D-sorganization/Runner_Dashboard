// @vitest-environment jsdom
/**
 * ProjectCard header (#1718): at 1280px a long repo name pushed "Run steward
 * now" past the card edge, where `overflow: hidden` clipped it.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
});
