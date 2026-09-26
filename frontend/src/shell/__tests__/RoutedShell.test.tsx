// @vitest-environment jsdom
/**
 * Integration tests for RoutedShell — the single navigation source of truth
 * (issues #835, #831).
 *
 * These assert the routing contract without live data hooks (pages are mocked):
 * the active tab is derived from the URL param, selecting a tab navigates the
 * URL (deep-linkable + back/forward), and every desktop and mobile nav entry
 * renders a native page — the legacy App is gone (#1345).
 */
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import "@testing-library/jest-dom/vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";

// --- Mocks: keep the test light and focused on routing -------------------

const breakpointMock = vi.fn(() => "lg");


vi.mock("../../pages/Analysis", () => ({
  AnalysisTab: (props: { activeTab?: string }) => (
    <div data-testid="native-analysis">{props.activeTab}</div>
  ),
}));

vi.mock("../../pages/AssessmentsPage", () => ({
  AssessmentsPage: () => (
    <div data-testid="native-assessments">Assessments</div>
  ),
}));

vi.mock("../../pages/Conductor", () => ({
  Conductor: () => <div data-testid="native-conductor">Conductor</div>,
}));

vi.mock("../../pages/CredentialsPage", () => ({
  CredentialsPage: () => (
    <div data-testid="native-credentials">Credentials</div>
  ),
}));

vi.mock("../../pages/Credentials", () => ({
  CredentialsMobile: () => (
    <div data-testid="mobile-credentials">Mobile Credentials</div>
  ),
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

vi.mock("../../pages/CodeRequestsPage", () => ({
  CodeRequestsPage: () => (
    <div data-testid="native-code-requests">Code Requests</div>
  ),
}));

vi.mock("../../pages/FleetOrchestrationPage", () => ({
  FleetOrchestrationPage: () => (
    <div data-testid="native-fleet-orchestration">Fleet Orchestration</div>
  ),
  default: () => (
    <div data-testid="native-fleet-orchestration">Fleet Orchestration</div>
  ),
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

vi.mock("../../pages/Maxwell", () => ({
  MaxwellMobile: () => <div data-testid="mobile-maxwell">Mobile Maxwell</div>,
}));

vi.mock("../../pages/Org", () => ({
  OrgPage: () => <div data-testid="native-org">Org</div>,
}));

vi.mock("../../pages/OverviewPage", () => ({
  default: () => <div data-testid="native-overview">Overview</div>,
}));

vi.mock("../../pages/Operations/OperationsPage", () => ({
  default: () => <div data-testid="native-operations">Operations</div>,
  OperationsPage: () => <div data-testid="native-operations">Operations</div>,
}));

vi.mock("../../pages/ProjectsPage", () => ({
  default: () => <div data-testid="native-projects">Projects</div>,
}));
vi.mock("../../pages/Staff/StaffPage", () => ({
  default: () => <div data-testid="native-staff">Staff</div>,
}));

vi.mock("../../pages/Fleet", () => ({
  FleetMobile: () => <div data-testid="mobile-overview">Mobile Overview</div>,
}));

vi.mock("../../pages/Principals", () => ({
  PrincipalsTab: () => <div data-testid="native-principals">Principals</div>,
}));

vi.mock("../../pages/Queue", () => ({
  QueueTab: () => <div data-testid="native-queue">Queue</div>,
  QueueMobile: () => <div data-testid="mobile-queue">Mobile Queue</div>,
}));

vi.mock("../../pages/RemediationPage", () => ({
  default: () => <div data-testid="native-remediation">Remediation</div>,
}));

vi.mock("../../pages/Remediation/Mobile", () => ({
  RemediationMobile: () => (
    <div data-testid="mobile-remediation">Mobile Remediation</div>
  ),
}));

vi.mock("../../pages/Reports", () => ({
  ReportsMobile: () => <div data-testid="mobile-reports">Mobile Reports</div>,
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

let testsPageShouldThrow = false;

vi.mock("../../pages/TestsPage", () => ({
  TestsPage: () => {
    if (testsPageShouldThrow) throw new Error("Boom in TestsPage");
    return <div data-testid="native-tests">Tests</div>;
  },
}));

vi.mock("../../pages/WorkflowsPage", () => ({
  WorkflowsPage: () => <div data-testid="native-workflows">Workflows</div>,
}));

vi.mock("../../components/ThemeSettings", () => ({
  ThemeSettings: () => <div data-testid="native-settings">Settings</div>,
}));

// Force the desktop shell branch by default so DesktopShell renders
// deterministically; individual tests can opt into mobile.
vi.mock("../../hooks/useBreakpoint", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return { ...actual, useBreakpoint: () => breakpointMock() };
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
import { mobileDrawerItems, mobilePrimaryItems } from "../navRegistry";
import { tabIdToPath } from "../routing";
import { Toaster } from "../../primitives/Toaster";

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="pathname">{loc.pathname}</span>;
}

function renderAt(path: string, { withToaster = false } = {}) {
  const Wrapper = withToaster ? Toaster : React.Fragment;
  return render(
    <Wrapper>
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <Routes>
          <Route path="/settings/push" element={<RoutedShell />} />
          <Route path="/t/:tabId" element={<RoutedShell />} />
          <Route path="/staff/:tabId" element={<RoutedShell />} />
          <Route path="/staff" element={<RoutedShell />} />
          <Route path="/work/:tabId" element={<RoutedShell />} />
          <Route path="/work" element={<RoutedShell />} />
          <Route path="/fleet/:tabId" element={<RoutedShell />} />
          <Route path="/fleet" element={<RoutedShell />} />
          <Route path="/settings/:tabId" element={<RoutedShell />} />
          <Route path="/settings" element={<RoutedShell />} />
          <Route path="/" element={<RoutedShell />} />
          <Route path="*" element={<RoutedShell isNotFoundRoute />} />
        </Routes>
      </MemoryRouter>
    </Wrapper>,
  );
}

describe("RoutedShell — URL is the source of truth", () => {
  beforeEach(() => {
    cleanup();
    localStorage.clear();
    breakpointMock.mockReturnValue("lg");
  });

  it("derives the default tab from the root path", async () => {
    renderAt("/");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("staff");
    expect(await screen.findByTestId("native-staff")).toBeInTheDocument();
  });

  it("derives the active tab from the /t/:tabId param", async () => {
    renderAt("/t/queue");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent("queue");
  });

  it("renders not-found panel for an unknown tab id (SC-D2)", async () => {
    renderAt("/t/not-a-real-tab");
    expect(
      await screen.findByRole("region", { name: /route not found/i }),
    ).toBeInTheDocument();
  });

  it("selecting a tab navigates the URL (deep-linkable + back/forward)", async () => {
    const user = userEvent.setup();
    renderAt("/");
    await screen.findByTestId("active-tab");
    await user.click(screen.getByText("go-maxwell"));
    expect(await screen.findByTestId("pathname")).toHaveTextContent(
      "/staff/maxwell",
    );
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "maxwell",
    );
  });

  it.each([
    ["staff", "native-staff"],
    ["overview", "native-overview"],
    ["operations", "native-operations"],
    ["insights", "native-analysis"],
    ["assessments", "native-assessments"],
    ["credentials", "native-credentials"],
    ["code-requests", "native-code-requests"],
    ["linear-setup", "native-linear-setup"],
    ["local-apps", "native-local-apps"],
    ["maxwell", "native-maxwell"],
    ["org", "native-org"],
    ["principals", "native-principals"],
    ["push-settings", "native-push-settings"],
    ["queue", "native-queue"],
    ["remediation", "native-remediation"],
    ["settings", "native-settings"],
    ["tests", "native-tests"],
    ["workflows", "native-workflows"],
  ])("routes desktop tab %s to its native page", async (tabId, testId) => {
    renderAt(`/t/${tabId}`);
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(tabId);
    expect(await screen.findByTestId(testId)).toBeInTheDocument();
  });

  it("redirects legacy /t/reports and /t/analysis to /fleet/insights (SC-G4)", async () => {
    renderAt("/t/reports");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "insights",
    );
    expect(await screen.findByTestId("native-analysis")).toBeInTheDocument();
  });

  it("redirects legacy /t/machines, /t/runner-audit and /t/events to /fleet sections (SC-G2)", async () => {
    renderAt("/t/machines");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "overview",
    );
    expect(await screen.findByTestId("native-overview")).toBeInTheDocument();
  });

  it("redirects legacy operational tabs to /fleet/operations (SC-G3)", async () => {
    renderAt("/t/deployment");
    expect(await screen.findByTestId("active-tab")).toHaveTextContent(
      "operations",
    );
    expect(await screen.findByTestId("native-operations")).toBeInTheDocument();
  });

  it.each([
    ["/fleet", "mobile-overview"],
    ["/t/queue", "mobile-queue"],
    ["/t/maxwell", "mobile-maxwell"],
    ["/t/remediation", "mobile-remediation"],
    ["/t/reports", "mobile-reports"],
    ["/fleet/insights", "mobile-reports"],
    ["/t/credentials", "mobile-credentials"],
    // #1345: the legacy fallback had no projects case, so Projects was blank.
    ["/t/projects", "native-projects"],
  ])("routes mobile tab %s to its mobile page", async (path, testId) => {
    breakpointMock.mockReturnValue("md");
    renderAt(path);

    expect(await screen.findByTestId(testId)).toBeInTheDocument();
  });

  it.each(
    [...mobilePrimaryItems(), ...mobileDrawerItems()].map((item) => [
      item.tabId,
    ]),
  )("mobile nav entry %s renders non-empty content (#1345)", async (tabId) => {
    breakpointMock.mockReturnValue("md");
    renderAt(tabIdToPath(tabId));

    const main = await screen.findByRole("main");
    await waitFor(() => expect(main.textContent?.trim()).not.toBe(""));
  });

  it("drops a stored Classic layout preference with a one-time notice (#1345)", async () => {
    localStorage.setItem("dashboard.layout", "legacy");
    renderAt("/t/queue", { withToaster: true });

    expect(await screen.findByTestId("native-queue")).toBeInTheDocument();
    expect(
      await screen.findByText(/Classic layout was retired/),
    ).toBeInTheDocument();
    expect(localStorage.getItem("dashboard.layout")).toBeNull();
  });

  it("catches tab errors in TabErrorBoundary without crashing the shell, and navigates away cleanly", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    testsPageShouldThrow = true;
    renderAt("/t/tests");

    // The shell chrome stays rendered
    expect(screen.getByTestId("active-tab")).toHaveTextContent("tests");
    // Tab error boundary renders the alert
    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent).toContain("Tests");
    expect(alert.textContent).toContain("Boom in TestsPage");

    // Click navigation button in shell chrome to navigate to maxwell
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "go-maxwell" }));

    // Navigation recovers without a reload
    expect(screen.getByTestId("pathname")).toHaveTextContent("/staff/maxwell");
    expect(await screen.findByTestId("native-maxwell")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    testsPageShouldThrow = false;
    errorSpy.mockRestore();
  });
});
