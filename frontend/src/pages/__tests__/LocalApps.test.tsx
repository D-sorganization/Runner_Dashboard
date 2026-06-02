// @vitest-environment jsdom
/**
 * Tests for LocalApps.tsx — decomposition #836 pass 3.
 *
 * Covers the extracted "Local Tools" tab behaviour:
 * 1. Smoke render.
 * 2. Renders a row per app with drift/health/service badges.
 * 3. Headline badges count behind/unhealthy/dirty apps.
 * 4. Empty state distinguishes "no manifest" vs "no tools".
 * 5. Loading state.
 * 6. Refresh button invokes onRefresh.
 * 7. Pure predicates behave as expected.
 * 8. Error boundary degrades to a Retry affordance.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, fireEvent, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LocalAppsTab, type LocalApp, type LocalAppsData } from "../LocalApps";
import { localAppHasUpdateAvailable, localAppUnhealthy } from "../localAppStatus";

afterEach(cleanup);

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  vi.restoreAllMocks();
});

const APPS: LocalApp[] = [
  {
    name: "tool-current",
    drift: { available: true, behind: 0, ahead: 0, ref: "main" },
    health: { available: true, ok: true, status: "ok" },
    service_status: "active",
    deployed_version: "1.2.3",
  },
  {
    name: "tool-behind",
    drift: { available: true, behind: 4, ahead: 0, ref: "main" },
    health: { available: true, ok: false, status: "down" },
    service_status: "inactive",
    deployed_version: "0.9.0",
    dirty: true,
    dirty_files: ["a.py", "b.py"],
  },
];

const DATA: LocalAppsData = { tools: APPS, manifest_path: "/x/local_apps.json" };

describe("LocalAppsTab", () => {
  it("renders without throwing (smoke test)", () => {
    expect(() =>
      render(<LocalAppsTab data={{ tools: [] }} loading={false} onRefresh={() => {}} />),
    ).not.toThrow();
  });

  it("renders a row per app", () => {
    render(<LocalAppsTab data={DATA} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText("tool-current")).toBeInTheDocument();
    expect(screen.getByText("tool-behind")).toBeInTheDocument();
    expect(screen.getByText("✔ current")).toBeInTheDocument();
    expect(screen.getByText("▼ 4 behind")).toBeInTheDocument();
  });

  it("counts behind / unhealthy / dirty apps in the header badges", () => {
    render(<LocalAppsTab data={DATA} loading={false} onRefresh={() => {}} />);
    const header = document.querySelector(".section-header") as HTMLElement;
    expect(within(header).getByText("1 update available")).toBeInTheDocument();
    expect(within(header).getByText("1 unhealthy")).toBeInTheDocument();
    expect(within(header).getByText("1 dirty")).toBeInTheDocument();
  });

  it("shows the no-tools state when a manifest exists but is empty", () => {
    render(
      <LocalAppsTab
        data={{ tools: [], manifest_path: "/x/local_apps.json" }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    expect(screen.getByText(/No tools defined in local_apps.json/i)).toBeInTheDocument();
  });

  it("shows the no-manifest state when no manifest is present", () => {
    render(<LocalAppsTab data={{ tools: [] }} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/No local_apps.json manifest found/i)).toBeInTheDocument();
  });

  it("shows the loading state", () => {
    render(<LocalAppsTab data={{ tools: [] }} loading={true} onRefresh={() => {}} />);
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
  });

  it("invokes onRefresh when the refresh button is clicked", () => {
    const onRefresh = vi.fn();
    render(<LocalAppsTab data={DATA} loading={false} onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: /Refresh local tools/i }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("predicates classify update-available and unhealthy apps", () => {
    expect(localAppHasUpdateAvailable(APPS[1])).toBe(true);
    expect(localAppHasUpdateAvailable(APPS[0])).toBe(false);
    expect(localAppUnhealthy(APPS[1])).toBe(true);
    expect(localAppUnhealthy(APPS[0])).toBe(false);
  });

  it("degrades to a Retry affordance when a row throws during render", () => {
    // A getter that throws when the body reads `.tools` simulates a malformed
    // payload; the boundary should catch it.
    const bad = {} as LocalAppsData;
    Object.defineProperty(bad, "tools", {
      get() {
        throw new Error("boom");
      },
    });
    render(<LocalAppsTab data={bad} loading={false} onRefresh={() => {}} />);
    expect(screen.getByText(/Local Tools failed to render/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry loading data/i })).toBeInTheDocument();
  });
});
