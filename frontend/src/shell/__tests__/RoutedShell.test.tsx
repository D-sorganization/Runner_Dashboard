// @vitest-environment jsdom
/**
 * Integration tests for RoutedShell — the single navigation source of truth
 * (issues #835, #831).
 *
 * These assert the routing contract without dragging in the 17k-line legacy
 * App or live data hooks (both mocked): the active tab is derived from the URL
 * param, selecting a tab navigates the URL (deep-linkable + back/forward), and
 * the legacy App is loaded lazily (its module is only imported on demand).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

// --- Mocks: keep the test light and focused on routing -------------------

const legacyAppImport = vi.fn();

vi.mock("../../legacy/App", () => {
  legacyAppImport();
  return {
    default: (props: { activeTab?: string; initialTab?: string }) => (
      <div
        data-testid="legacy-app"
        data-active-tab={props.activeTab ?? props.initialTab}
      />
    ),
  };
});

vi.mock("../../pages/AgentDispatch", () => ({
  AgentDispatchPage: () => (
    <div data-testid="native-agent-dispatch">Agent Dispatch</div>
  ),
}));

vi.mock("../../pages/Analysis", () => ({
  AnalysisTab: (props: { activeTab?: string }) => (
    <div data-testid="native-analysis">{props.activeTab}</div>
  ),
}));

vi.mock("../../pages/ClineLauncher", () => ({
  ClineLauncherTab: () => (
    <div data-testid="native-cline-launcher">Cline Launcher</div>
  ),
}));

vi.mock("../../pages/Conductor", () => ({
  Conductor: () => <div data-testid="native-conductor">Conductor</div>,
}));

vi.mock("../../pages/CredentialsPage", () => ({
  CredentialsPage: () => <div data-testid="native-credentials">Credentials</div>,
}));

vi.mock("../../pages/Deployment", () => ({
  DeploymentTab: () => <div data-testid="native-deployment">Deployment</div>,
}));

vi.mock("../../pages/Diagnostics", () => ({
  DiagnosticsTab: () => <div data-testid="native-diagnostics">Diagnostics</div>,
}));

vi.mock("../../pages/Events", () => ({
  EventsTab: () => <div data-testid="native-events">Events</div>,
}));

vi.mock("../../pages/LinearSetup", () => ({
  LinearSetup: () => <div data-testid="native-linear-setup">Linear Setup</div>,
}));

vi.mock("../../pages/LocalApps", () => ({
  LocalAppsPage: () => <div data-testid="native-local-apps">Local Apps</div>,
}));

vi.mock("../../pages/Machines", () => ({
  MachinesPage: () => <div data-testid="native-machines">Machines</div>,
}));

vi.mock("../../pages/MaxwellPage", () => ({
  MaxwellPage: () => <div data-testid="native-maxwell">Maxwell</div>,
}));

vi.mock("../../pages/Org", () => ({
  OrgPage: () => <div data-testid="native-org">Org</div>,
}));

vi.mock("../../pages/Principals", () => ({
  PrincipalsTab: () => <div data-testid="native-principals">Principals</div>,
}));

vi.mock("../../pages/Queue", () => ({
  QueueTab: () => <div data-testid="native-queue">Queue</div>,
  QueueMobile: () => <div data-testid="mobile-queue">Mobile Queue</div>,
}));

vi.mock("../../pages/RunnerAudit", () => ({
  RunnerAuditPage: () => (
    <div data-testid="native-runner-audit">Runner Audit</div>
  ),
}));

vi.mock("../../pages/RunnerSchedule", () => ({
  RunnerSchedulePage: () => (
    <div data-testid="native-runner-schedule">Runner Schedule</div>
  ),
}));

vi.mock("../../pages/PushSettings", () => ({
  default: () => <div data-testid="native-push-settings">Push Settings</div>,
}));

vi.mock("../../pages/ScheduledJobs", () => ({
  default: () => <div data-testid="native-scheduled-jobs">Scheduled Jobs</div>,
}));

vi.mock("../../pages/TestsPage", () => ({
  TestsPage: () => <div data-testid="native-tests">Tests</div>,
}));

vi.mock("../../pages/WorkflowsPage", () => ({
  WorkflowsPage: () => <div data-testid="native-workflows">Workflows</div>,
}));

vi.mock("../../components/ThemeSettings", () => ({
  ThemeSettings: () => <div data-testid="native-settings">Settings</div>,
}));

// Force the desktop shell branch (lg) so DesktopShell renders deterministically.
vi.mock("../../hooks/useBreakpoint", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useBreakpoint: () => "lg" };
});

vi.mock("../../hooks/useSession", () => ({
  useSession: () => ({ loggedIn: false, refresh: vi.fn() }),
}));

vi.mock("../../lib/useProviderRegistry", () => ({
  useProviderRegistry: () => ({ registry: null }),
}));

vi.mock("../../design/ThemeContext", () => ({
  useThemeContext: () => ({ mode: "light", setMode: vi.fn() }),
}));

// Stub the heavy desktop shell with a thin harness that exposes activeTabId
// and a button that drives onSelect — exactly the contract RoutedShell relies
// on, without the full sidebar/toolstrip render.
vi.mock("../DesktopShell", () => ({
  DesktopShell: (props: {
    activeTabId: string;
    onSelect: (id: string) => void;
    children: React.ReactNode;
  }) => (
    <div>
      <span data-testid="active-tab">{props.activeTabId}</span>
      <button onClick={() => props.onSelect("maxwell")}>go-maxwell</button>
      {props.children}
    </div>
  ),
}));

import { RoutedShell } from "../RoutedShell";

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="pathname">{loc.pathname}</span>;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes>
        <Route path="/t/:tabId" element={<RoutedShell />} />
        <Route path="/" element={<RoutedShell />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RoutedShell — URL is the source of truth", () => {
  beforeEach(() => {
    cleanup();
    legacyAppImport.mockClear();
  });

  it("derives the default tab from the root path", async () => {
    renderAt("/");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "overview",
    );
  });

  it("derives the active tab from the /t/:tabId param", async () => {
    renderAt("/t/queue");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("queue");
  });

  it("falls back to the default tab for an unknown tab id", async () => {
    renderAt("/t/not-a-real-tab");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "overview",
    );
  });

  it("selecting a tab navigates the URL (deep-linkable + back/forward)", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await screen.findByTestId("active-tab");
    await user.click(screen.getByText("go-maxwell"));
    expect(await screen.findByTestId("pathname")).toHaveTextContent(
      "/t/maxwell",
    );
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "maxwell",
    );
  });

  it("lazy-loads the legacy App (code-split, not eager)", async () => {
    renderAt("/");
    // The legacy App renders only after its lazy chunk resolves.
    expect(await screen.findByTestId("legacy-app")).toBeInTheDocument();
  });

  it.each([
    ["agent-dispatch", "native-agent-dispatch"],
    ["analysis", "native-analysis"],
    ["cline-launcher", "native-cline-launcher"],
    ["conductor", "native-conductor"],
    ["credentials", "native-credentials"],
    ["deployment", "native-deployment"],
    ["diagnostics", "native-diagnostics"],
    ["events", "native-events"],
    ["linear-setup", "native-linear-setup"],
    ["local-apps", "native-local-apps"],
    ["machines", "native-machines"],
    ["maxwell", "native-maxwell"],
    ["org", "native-org"],
    ["principals", "native-principals"],
    ["push-settings", "native-push-settings"],
    ["queue", "native-queue"],
    ["reports", "native-analysis"],
    ["runner-audit", "native-runner-audit"],
    ["runner-schedule", "native-runner-schedule"],
    ["scheduled-jobs", "native-scheduled-jobs"],
    ["settings", "native-settings"],
    ["tests", "native-tests"],
    ["workflows", "native-workflows"],
  ])(
    "routes self-contained desktop tab %s without mounting the legacy App",
    async (tabId, testId) => {
      renderAt(`/t/${tabId}`);
      expect(await screen.findByTestId("active-tab")).toHaveTextContent(tabId);
      expect(await screen.findByTestId(testId)).toBeInTheDocument();
      expect(screen.queryByTestId("legacy-app")).not.toBeInTheDocument();
    },
  );

  it("keeps legacy fallback for tabs that still depend on legacy-owned state", async () => {
    renderAt("/t/fleet-orchestration");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "fleet-orchestration",
    );
    expect(await screen.findByTestId("legacy-app")).toHaveAttribute(
      "data-active-tab",
      "fleet-orchestration",
    );
  });
});
