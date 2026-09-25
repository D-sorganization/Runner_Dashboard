/**
 * RoutedShell.tsx — the dashboard's single navigation source of truth
 * (issues #835, #831).
 *
 * The active tab is derived from the browser URL via React Router (see
 * `routing.ts`), so every `navRegistry` tab is a real, deep-linkable route:
 * operators can bookmark/share a tab and browser back/forward traverse tabs.
 * Selecting a tab navigates the URL; the URL is the state. This retires the
 * previous hand-rolled `window.location.pathname` + React-state navigation in
 * `main.tsx`.
 *
 * The legacy `App` is retained only for the explicit legacy desktop escape
 * hatch and the still-legacy mobile fallback. Modern desktop routes render
 * native page modules directly and never use legacy/App.tsx as a silent
 * desktop fallback (#949).
 *
 * Law of Demeter: the shell receives a flat `activeTab` string and an
 * `onSelectTab(tabId)` callback; it never reaches into router internals.
 */
import React, { useCallback } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { MobileShell, type TabId } from "./MobileShell";
import { DesktopShell } from "./DesktopShell";
import { ActiveProviderControl } from "./ActiveProviderControl";
import { HelpAbout } from "./HelpAbout";
import { introForTab } from "./intro";
import { resolveDesktopShellLayout } from "./layoutFlag";
import { buildShellActions } from "./shellActions";
import {
  DEFAULT_TAB_ID,
  normalizeTabId,
  pathnameToTabId,
  tabIdToPath,
  getTabRedirect,
} from "./routing";
import { NotFoundPanel } from "./NotFoundPanel";
import { useToast } from "../primitives/Toaster";
import { IntroHeader } from "../primitives/IntroHeader";
import { ConnectionIndicator } from "../primitives/ConnectionIndicator";
import { useSession } from "../hooks/useSession";
import { useProviderRegistry } from "../lib/useProviderRegistry";
import { useBreakpoint } from "../hooks/useBreakpoint";
import { useThemeContext } from "../design/ThemeContext";
import { ThemeSelector } from "../components/ThemeSelector";
import { DensityToggle } from "../components/DensityToggle";
import { QueueMobile } from "../pages/Queue";
import { QueueTab } from "../pages/Queue";
import { MaxwellMobile } from "../pages/Maxwell";
import { ReportsMobile } from "../pages/Reports";
import { CredentialsMobile } from "../pages/Credentials";
import { FleetMobile } from "../pages/Fleet";
import {
  RemediationMobile,
  type InFlightDispatch,
} from "../pages/Remediation/Mobile";
import { AgentDispatchPage } from "../pages/AgentDispatch";
import { AnalysisTab } from "../pages/Analysis";
import { AssessmentsPage } from "../pages/AssessmentsPage";
import { ClineLauncherTab } from "../pages/ClineLauncher";
import { Conductor } from "../pages/Conductor";
import { CredentialsPage } from "../pages/CredentialsPage";
import { DeploymentTab } from "../pages/Deployment";
import { DiagnosticsTab } from "../pages/Diagnostics";
import { EventsTab } from "../pages/Events";
import { FeatureRequestsPage } from "../pages/FeatureRequestsPage";
import { LinearSetup } from "../pages/LinearSetup";
import { LocalAppsPage } from "../pages/LocalApps";
import { MachinesPage } from "../pages/Machines";
import { MaxwellPage } from "../pages/MaxwellPage";
import { OrgPage } from "../pages/Org";
import { PrincipalsTab } from "../pages/Principals";
import { RunnerAuditPage } from "../pages/RunnerAudit";
import { RunnerSchedulePage } from "../pages/RunnerSchedule";
import { TestsPage } from "../pages/TestsPage";
import { WorkflowsPage } from "../pages/WorkflowsPage";
import PushSettings from "../pages/PushSettings";
import ScheduledJobs from "../pages/ScheduledJobs";
import { ThemeSettings } from "../components/ThemeSettings";
import { TabErrorBoundary } from "../primitives/TabErrorBoundary";
import { SkeletonCard } from "../primitives/Skeleton";
import { navItemById } from "./navRegistry";

// The legacy App is isolated behind the explicit legacy layout flag and mobile
// fallback while the modern desktop shell routes registered tabs natively.
const LazyLegacyApp = React.lazy(() => import("../legacy/App"));
const LazyFleetOrchestrationPage = React.lazy(
  () => import("../pages/FleetOrchestrationPage"),
);
const LazyOverviewPage = React.lazy(() => import("../pages/OverviewPage"));
const LazyRemediationPage = React.lazy(
  () => import("../pages/RemediationPage"),
);
const LazyStaffPage = React.lazy(() => import("../pages/Staff/StaffPage"));
const LazyProjectsPage = React.lazy(() => import("../pages/ProjectsPage"));
const LazyFleetCommandPage = React.lazy(
  () => import("../pages/FleetCommand/FleetCommandPage"),
);

/**
 * Persistent/global provider control for the shell topbar (#811). Fetches the
 * unified registry once and renders the always-visible ActiveProviderControl;
 * renders nothing until the registry is available so the topbar never flashes a
 * broken control. Clicking "Fix login" jumps to the Credentials tab.
 */
function ShellActiveProvider({
  onRequestLogin,
}: {
  onRequestLogin: () => void;
}) {
  const { registry } = useProviderRegistry();
  if (!registry) return null;
  return (
    <ActiveProviderControl
      registry={registry}
      onRequestLogin={onRequestLogin}
    />
  );
}

/**
 * Persistent theme picker for the desktop shell header (#820). Reads the shared
 * theme context (single source of truth via ThemeProvider/useTheme) so all 13
 * fleet themes are reachable from the always-visible topbar.
 */
function ShellThemeSelector() {
  const { mode, setMode } = useThemeContext();
  return <ThemeSelector currentMode={mode} onThemeChange={setMode} />;
}

function nativeDesktopTabContent(tabId: string): React.ReactNode | null {
  switch (normalizeTabId(tabId)) {
    case "overview":
      return <LazyOverviewPage />;
    case "agent-dispatch":
      return <AgentDispatchPage />;
    case "insights":
    case "analysis":
    case "reports":
      return <AnalysisTab activeTab={tabId} />;
    case "assessments":
      return <AssessmentsPage />;
    case "cline-launcher":
      return <ClineLauncherTab />;
    case "conductor":
      return <Conductor />;
    case "credentials":
      return <CredentialsPage />;
    case "deployment":
      return <DeploymentTab />;
    case "diagnostics":
      return <DiagnosticsTab />;
    case "events":
      return <EventsTab />;
    case "feature-requests":
      return <FeatureRequestsPage />;
    case "fleet-command":
      return <LazyFleetCommandPage />;
    case "fleet-orchestration":
      return <LazyFleetOrchestrationPage />;
    case "linear-setup":
      return <LinearSetup />;
    case "local-apps":
      return <LocalAppsPage />;
    case "machines":
      return <MachinesPage />;
    case "maxwell":
      return <MaxwellPage />;
    case "org":
      return <OrgPage />;
    case "principals":
      return <PrincipalsTab />;
    case "projects":
      return <LazyProjectsPage />;
    case "push-settings":
      return <PushSettings />;
    case "queue":
      return <QueueTab />;
    case "remediation":
      return <LazyRemediationPage />;
    case "runner-audit":
      return <RunnerAuditPage />;
    case "runner-schedule":
      return <RunnerSchedulePage />;
    case "scheduled-jobs":
      return <ScheduledJobs />;
    case "settings":
      return <ThemeSettings />;
    case "staff":
      return <LazyStaffPage />;
    case "tests":
      return <TestsPage />;
    case "workflows":
      return <WorkflowsPage />;
    default:
      return null;
  }
}

/**
 * RoutedShell wires the shell to the URL: the active tab is read from the
 * `:tabId` route param (falling back to the default), and selecting a tab
 * navigates to that tab's canonical path. Because the URL is the single source
 * of truth, bookmarks and back/forward work for free.
 */
export function RoutedShell({
  isNotFoundRoute,
}: {
  isNotFoundRoute?: boolean;
} = {}) {
  const navigate = useNavigate();
  const location = useLocation();
  const { showToast } = useToast();

  const redirect = getTabRedirect(location.pathname);
  React.useEffect(() => {
    if (redirect) {
      const key = `redirect_toast_${redirect.to}`;
      let hasNotified = false;
      try {
        hasNotified = Boolean(sessionStorage.getItem(key));
        if (!hasNotified) sessionStorage.setItem(key, "1");
      } catch {
        // Storage unavailable
      }
      if (!hasNotified) {
        showToast(`${redirect.label} moved to ${redirect.to}`, {
          title: "Page Moved",
          variant: "info",
        });
      }
      navigate(redirect.to, { replace: true });
    }
  }, [redirect, navigate, showToast]);

  const resolvedTab = isNotFoundRoute
    ? undefined
    : pathnameToTabId(location.pathname);

  const isNotFound = isNotFoundRoute || resolvedTab === undefined;
  const activeTab = resolvedTab ?? DEFAULT_TAB_ID;

  const onSelectTab = useCallback(
    (tabId: string) => {
      navigate(tabIdToPath(tabId));
    },
    [navigate],
  );

  // Anonymous local page usage telemetry (issue #1302 / SC-G1)
  React.useEffect(() => {
    if (!activeTab || isNotFound) return;
    try {
      fetch("/api/usage/page-view", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tab_id: activeTab,
          pathname:
            typeof window !== "undefined" ? window.location.pathname : undefined,
        }),
      }).catch(() => {
        // Non-blocking telemetry
      });
    } catch {
      // Non-blocking
    }
  }, [activeTab, isNotFound]);

  return (
    <AppShell
      activeTab={activeTab}
      onSelectTab={onSelectTab}
      isNotFound={isNotFound}
      unmatchedPath={location.pathname}
    />
  );
}

/**
 * AppShell renders mobile or desktop chrome for the given active tab.
 * Navigation is delegated to `onSelectTab` (the router), so URL state remains
 * the single source of truth.
 */
export function AppShell({
  activeTab,
  onSelectTab,
  isNotFound,
  unmatchedPath,
}: {
  activeTab: string;
  onSelectTab: (tabId: string) => void;
  isNotFound?: boolean;
  unmatchedPath?: string;
}) {
  const breakpoint = useBreakpoint();
  const isMobile = breakpoint !== "lg" && breakpoint !== "xl";

  // Reactive dashboard-session state (#842): the topbar Login/Logout label
  // derives from this hook, so it updates on focus / visibility / logout.
  const { loggedIn, refresh: refreshSession } = useSession();

  // Per-tab intro header dismissal (#822).
  const [dismissedIntros, setDismissedIntros] = React.useState<
    Record<string, boolean>
  >({});
  const [mobileRemediationDispatches, setMobileRemediationDispatches] =
    React.useState<InFlightDispatch[]>([]);

  // The legacy App emits tab changes through its own toolstrip / mobile UI;
  // route those through the router so the URL stays authoritative.
  const handleLegacyTabChange = useCallback(
    (nextLegacyTab: string) => {
      onSelectTab(normalizeTabId(nextLegacyTab));
    },
    [onSelectTab],
  );

  if (isMobile) {
    if (isNotFound) {
      return (
        <MobileShell
          currentTab="staff"
          onTabChange={(t) => onSelectTab(t)}
        >
          <NotFoundPanel
            path={unmatchedPath || "/"}
            onNavigateHome={() => onSelectTab("staff")}
          />
        </MobileShell>
      );
    }

    const mobileTab = normalizeTabId(activeTab) as TabId;
    const mobileTabContent = {
      overview: <FleetMobile />,
      queue: <QueueMobile />,
      maxwell: <MaxwellMobile />,
      remediation: (
        <RemediationMobile
          inFlightDispatches={mobileRemediationDispatches}
          onAddInFlight={(dispatch) =>
            setMobileRemediationDispatches((prev) => [...prev, dispatch])
          }
        />
      ),
      reports: <ReportsMobile />,
      insights: <ReportsMobile />,
      credentials: <CredentialsMobile />,
      staff: <LazyStaffPage />,
      "fleet-command": <LazyFleetCommandPage />,
    } as Partial<Record<TabId, React.ReactNode>>;
    const nativeMobileContent = mobileTabContent[mobileTab];
    const legacyMobileFallback = nativeMobileContent ? null : (
      <LazyLegacyApp
        initialTab={mobileTab}
        activeTab={mobileTab}
        onTabChange={handleLegacyTabChange}
      />
    );

    return (
      <MobileShell
        currentTab={mobileTab}
        onTabChange={(t) => onSelectTab(t)}
        tabContent={mobileTabContent as Record<TabId, React.ReactNode>}
      >
        {legacyMobileFallback}
      </MobileShell>
    );
  }

  // Desktop. The modern shell (#802) is the default but fully reversible: when
  // the layout flag resolves to legacy we render the untouched legacy App with
  // its own top toolstrip. Otherwise DesktopShell owns navigation and renders
  // native page content for every registered tab (#949).
  const env = (import.meta.env as Record<string, string | undefined>)
    ?.VITE_DESKTOP_SHELL;
  const useModernShell = resolveDesktopShellLayout({ env });

  if (!useModernShell) {
    return (
      <TabErrorBoundary tabName="Legacy Dashboard" resetKey={activeTab}>
        <React.Suspense
          fallback={
            <div
              role="status"
              aria-label="Loading legacy dashboard…"
              className="tab-loading-skeleton"
              style={{ padding: 24, maxWidth: 1440, margin: "0 auto" }}
            >
              <SkeletonCard lines={4} />
            </div>
          }
        >
          <LazyLegacyApp
            initialTab={activeTab}
            activeTab={activeTab}
            onTabChange={handleLegacyTabChange}
          />
        </React.Suspense>
      </TabErrorBoundary>
    );
  }

  const intro = introForTab(activeTab);
  const introNode =
    intro && !dismissedIntros[activeTab] ? (
      <IntroHeader
        title={intro.title}
        body={intro.body}
        onDismiss={() =>
          setDismissedIntros((prev) => ({ ...prev, [activeTab]: true }))
        }
      />
    ) : undefined;
  const navItem = navItemById(activeTab);
  const tabDisplayName = navItem ? navItem.label : activeTab;
  const nativeContent = nativeDesktopTabContent(activeTab);
  const wrappedContent = isNotFound ? (
    <NotFoundPanel
      path={unmatchedPath || "/"}
      onNavigateHome={() => onSelectTab("staff")}
    />
  ) : (
    <TabErrorBoundary tabName={tabDisplayName} resetKey={activeTab}>
      <React.Suspense
        fallback={
          <div
            role="status"
            aria-label={`Loading ${tabDisplayName}…`}
            className="tab-loading-skeleton"
            style={{ padding: 24, maxWidth: 1440, margin: "0 auto" }}
          >
            <SkeletonCard lines={4} />
          </div>
        }
      >
        {nativeContent}
      </React.Suspense>
    </TabErrorBoundary>
  );

  return (
    <DesktopShell
      activeTabId={activeTab}
      onSelect={onSelectTab}
      actions={buildShellActions(loggedIn, refreshSession)}
      helpAbout={<HelpAbout onNavigate={onSelectTab} />}
      intro={introNode}
      headerExtra={
        <>
          <ConnectionIndicator />
          <DensityToggle />
          <ShellThemeSelector />
          <ShellActiveProvider
            onRequestLogin={() => onSelectTab("credentials")}
          />
        </>
      }
    >
      {wrappedContent}
    </DesktopShell>
  );
}
