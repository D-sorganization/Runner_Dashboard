// @ts-nocheck
/* eslint-disable */
// NOTE: This directive MUST stay on line 1 — TypeScript only honors
// `@ts-nocheck` when it is the first comment in the file (before any
// statements). It was previously placed after the imports, so it was
// silently ignored and the legacy file leaked ~1000 type errors into any
// `tsc` run. Decomposition of this file is tracked separately (#403).
import React from "react"
import { legacyFetch } from "../lib/api"
import * as fleetAlerts from "../lib/fleetAlerts"
import { AgentDispatchPage } from "../pages/AgentDispatch"
import { Conductor } from "../pages/Conductor"
import { QueueTab } from "../pages/Queue"
import { LinearSetup } from "../pages/LinearSetup"
import PushSettings from "../pages/PushSettings"
import { ClineLauncherTab } from "../pages/ClineLauncher"
import { DiagnosticsTab } from "../pages/Diagnostics"
import { PrincipalsTab } from "../pages/Principals"
import RunnerAudit from "../pages/RunnerAudit"
import ScheduledJobs from "../pages/ScheduledJobs"
import { OrgTab } from "../pages/Org"
import { TestsTab } from "../pages/Tests"
import { LocalAppsTab } from "../pages/LocalApps"
import { localAppNeedsAttention } from "../pages/localAppStatus"
import { WorkflowsTab } from "../pages/Workflows"
import { AssessmentsTab } from "../pages/Assessments"
import { DeploymentTab } from "../pages/Deployment"
import { FleetOrchestrationTab } from "../pages/FleetOrchestration"
import { CredentialsTab } from "../pages/CredentialsPage"
import { MaxwellTab } from "../pages/MaxwellPage"
import { RunnerScheduleTab } from "../pages/RunnerSchedule"
import { FeatureRequestsTab } from "../pages/FeatureRequests"
import { AnalysisTab } from "../pages/Analysis"
import { isAnalysisTabKey } from "../lib/analysisTabs"
import { AlertsCenter } from "../primitives/AlertsCenter"
import { EventsTab, OverviewEventSection } from "../pages/Events"
import { RemediationTab } from "../pages/RemediationTab"
import { useFleetEvents } from "../hooks/useFleetEvents"
import { RecoveryDialog } from "./RecoveryDialog"
import { SessionExpiredDialog } from "./SessionExpiredDialog"
import { marked } from "marked"
import DOMPurify from "dompurify"
import {
  emitSessionExpired,
  subscribeSessionExpired,
  shouldIgnoreUnauthorizedResponse,
  tryRefreshSession,
} from "./sessionExpired"
import { installWheelValueGuard } from "./wheelValueGuard"
import { ThemeSettings } from "../components/ThemeSettings"
import { AssistantSidebar, DashboardHelp } from "../pages/AssistantSidebar"
import { QuickDispatchPopover } from "../pages/QuickDispatch"
import { MachinesTab } from "../pages/Machines"
import { FleetTab } from "../pages/FleetTab"
import { OverviewLeases } from "../pages/OverviewLeases"
import { shortSha } from "../lib/fleetTelemetry"
import {
  ASST_LS,
  clearAssistantTranscriptHistory,
  lsGet,
  lsSet,
} from "../lib/assistantStorage"
import { createVisibleInterval } from "./visibleInterval"
import { installLegacyFetchGuards } from "./fetchGuards"

var h = React.createElement;

// Wrap global fetch to detect session-expiry 401s and prompt login through React.
installLegacyFetchGuards({
  emitSessionExpired,
  shouldIgnoreUnauthorizedResponse,
  tryRefreshSession,
});
// ────────────────────────────────────────────────────────────────────────

// Configure marked with safe options (issue #7)
if (typeof marked !== "undefined") {
  marked.use({ mangle: false, headerIds: false, gfm: true });
}

function icon(path, s) {
  s = s || 16;
  return h(
    "svg",
    {
      width: s,
      height: s,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    },
    h("path", { d: path }),
  );
}

var I = {
  chevronDown: function (s) {
    return icon("M6 9l6 6 6-6", s);
  },
  server: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("rect", { x: 2, y: 2, width: 20, height: 8, rx: 2 }),
      h("rect", { x: 2, y: 14, width: 20, height: 8, rx: 2 }),
      h("circle", { cx: 6, cy: 6, r: 1, fill: "currentColor" }),
      h("circle", { cx: 6, cy: 18, r: 1, fill: "currentColor" }),
    );
  },
  cpu: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("rect", { x: 4, y: 4, width: 16, height: 16, rx: 2 }),
      h("rect", { x: 9, y: 9, width: 6, height: 6 }),
      h("path", {
        d: "M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3",
      }),
    );
  },
  activity: function (s) {
    return icon("M22 12h-4l-3 9L9 3l-3 9H2", s);
  },
  gitPR: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 18, cy: 18, r: 3 }),
      h("circle", { cx: 6, cy: 6, r: 3 }),
      h("path", { d: "M13 6h3a2 2 0 012 2v7M6 9v12" }),
    );
  },
  issue: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 12, cy: 12, r: 10 }),
      h("line", { x1: 12, y1: 8, x2: 12, y2: 12 }),
      h("line", { x1: 12, y1: 16, x2: 12.01, y2: 16 }),
    );
  },
  settings: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 12, cy: 12, r: 3 }),
      h("path", {
        d: "M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1 1.54V21a2 2 0 11-4 0v-.09a1.7 1.7 0 00-1-1.54 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.7 1.7 0 00.34-1.87 1.7 1.7 0 00-1.54-1H3a2 2 0 110-4h.09a1.7 1.7 0 001.54-1 1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06a1.7 1.7 0 001.87.34H9a1.7 1.7 0 001-1.54V3a2 2 0 114 0v.09a1.7 1.7 0 001 1.54 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06a1.7 1.7 0 00-.34 1.87V9c.25.61.85 1 1.54 1H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.54 1z",
      }),
    );
  },
  repo: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", { d: "M4 19.5A2.5 2.5 0 016.5 17H20" }),
      h("path", {
        d: "M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z",
      }),
    );
  },
  play: function (s) {
    return icon("M5 3l14 9-14 9V3z", s);
  },
  stop: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
      },
      h("rect", { x: 6, y: 6, width: 12, height: 12, rx: 1 }),
    );
  },
  arrowUp: function (s) {
    return icon("M12 19V5M5 12l7-7 7 7", s);
  },
  arrowDown: function (s) {
    return icon("M12 5v14M5 12l7 7 7-7", s);
  },
  refresh: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", { d: "M23 4v6h-6M1 20v-6h6" }),
      h("path", {
        d: "M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15",
      }),
    );
  },
  flask: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", {
        d: "M9 3h6M10 3v7.4a2 2 0 01-.5 1.3L4 19a2 2 0 001.5 3h13a2 2 0 001.5-3l-5.5-7.3a2 2 0 01-.5-1.3V3",
      }),
    );
  },
  fileText: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("path", {
        d: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z",
      }),
      h("polyline", { points: "14 2 14 8 20 8" }),
      h("line", { x1: 16, y1: 13, x2: 8, y2: 13 }),
      h("line", { x1: 16, y1: 17, x2: 8, y2: 17 }),
    );
  },
  docker: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("rect", { x: 1, y: 10, width: 22, height: 10, rx: 2 }),
      h("rect", { x: 5, y: 6, width: 4, height: 4 }),
      h("rect", { x: 10, y: 6, width: 4, height: 4 }),
      h("rect", { x: 10, y: 2, width: 4, height: 4 }),
    );
  },
  queue: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("line", { x1: 8, y1: 6, x2: 21, y2: 6 }),
      h("line", { x1: 8, y1: 12, x2: 21, y2: 12 }),
      h("line", { x1: 8, y1: 18, x2: 21, y2: 18 }),
      h("circle", { cx: 3, cy: 6, r: 1, fill: "currentColor" }),
      h("circle", { cx: 3, cy: 12, r: 1, fill: "currentColor" }),
      h("circle", { cx: 3, cy: 18, r: 1, fill: "currentColor" }),
    );
  },
  clock: function (s) {
    return h(
      "svg",
      {
        width: s || 16,
        height: s || 16,
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        strokeWidth: 2,
        strokeLinecap: "round",
        strokeLinejoin: "round",
      },
      h("circle", { cx: 12, cy: 12, r: 10 }),
      h("polyline", { points: "12 6 12 12 16 14" }),
    );
  },
};

// ════════════════════════ ANALYSIS TAB (orchestrator) ════════════════════════
// AnalysisTab (the sub-tab orchestrator) and its leaf panels are extracted to
// ../pages/Analysis; the shared `isAnalysisTabKey` routing predicate lives in
// ../lib/analysisTabs and is consumed by the shell below (#836, pass 12).

// ════════════════════════ LOCAL APPS TAB ════════════════════════
// The local-app status predicates (localAppHasUpdateAvailable / Unhealthy /
// NeedsAttention) live in ../pages/localAppStatus (decomposition #836); the
// duplicated inline copies were removed here in pass 12 and the one call site
// now consumes the lib export directly.

// ════════════════════════ MAIN APP ════════════════════════


function App({ initialTab, onTabChange, activeTab, chromeless }: { initialTab?: string; onTabChange?: (tab: string) => void; activeTab?: string; chromeless?: boolean } = {}) {
  // When `activeTab` is supplied the component is *controlled* by the new
  // desktop shell (#802): the shell owns navigation (sidebar + slim toolstrip)
  // and App just renders the page body for the requested tab. When it is
  // omitted, App is uncontrolled and keeps its own internal tab state plus its
  // legacy top toolstrip — preserving the reversible legacy shell.
  var ts = React.useState(initialTab ?? "overview");
  var internalTab = ts[0],
    _setTabInternal = ts[1];
  var tab = activeTab != null ? activeTab : internalTab;
  var setTab = React.useCallback(function(nextTab: string) {
    _setTabInternal(nextTab);
    if (onTabChange) onTabChange(nextTab);
  }, [onTabChange]);
  // Conductor integration (issue #1282): probe the orchestrator surface once.
  // The tab is only shown when the backend feature flag is enabled (the probe
  // returns non-404). This keeps the surface inert/reversible by default and
  // orthogonal — a probe failure never blocks other tabs.

  // Fleet event feed (issue #863): durable, persisted history of runner/disk
  // events. The event-derived alerts (offline + disk pressure) are folded into
  // the consolidated AlertsCenter pill so the header surfaces "runner(s)
  // offline — disk pressure" without a screen-covering pop-up.
  var fleetEvents = useFleetEvents();

  var conductorState = React.useState(false);
  var conductorEnabled = conductorState[0],
    setConductorEnabled = conductorState[1];
  React.useEffect(function () {
    legacyFetch("/api/orchestrator/queue", { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { setConductorEnabled(r.status !== 404); })
      .catch(function () { setConductorEnabled(false); });
  }, []);
  var rs = React.useState([]);
  var runners = rs[0],
    setRunners = rs[1];
  var ws = React.useState([]);
  var runs = ws[0],
    setRuns = ws[1];
  var er = React.useState([]);
  var enrichedRuns = er[0],
    setEnrichedRuns = er[1];
  var wd = React.useState({});
  var watchdog = wd[0],
    setWatchdog = wd[1];
  var ghs = React.useState({});
  var githubStatus = ghs[0],
    setGithubStatus = ghs[1];
  var ss = React.useState({});
  var system = ss[0],
    setSystem = ss[1];
  var xs = React.useState({});
  var stats = xs[0],
    setStats = xs[1];
  var os = React.useState([]);
  var repos = os[0],
    setRepos = os[1];
  var rl = React.useState(false);
  var reposLoading = rl[0],
    setReposLoading = rl[1];
  var al = React.useState(false);
  var actionLoading = al[0],
    setActionLoading = al[1];
  var cs = React.useState(true);
  var connected = cs[0],
    setConnected = cs[1];
  var ls = React.useState(null);
  var lastUpdate = ls[0],
    setLastUpdate = ls[1];
  var tr = React.useState([]);
  var testRepos = tr[0],
    setTestRepos = tr[1];
  var tl = React.useState(false);
  var testsLoading = tl[0],
    setTestsLoading = tl[1];
  var cr = React.useState([]);
  var ciResults = cr[0],
    setCiResults = cr[1];
  var rp = React.useState([]);
  var reports = rp[0],
    setReports = rp[1];
  var rpl = React.useState(false);
  var reportsLoading = rpl[0],
    setReportsLoading = rpl[1];
  var pr = React.useState(null);
  var principal = pr[0],
    setPrincipal = pr[1];
  var mcv = React.useState(function () {
    return window.matchMedia ? window.matchMedia("(max-width: 768px)").matches : false;
  });
  var mobileCredentialsViewport = mcv[0],
    setMobileCredentialsViewport = mcv[1];
  var qs = React.useState({});
  var queue = qs[0],
    setQueue = qs[1];
  var ql = React.useState(false);
  var queueLoading = ql[0],
    setQueueLoading = ql[1];
  var ms = React.useState({});
  var machinesData = ms[0],
    setMachinesData = ms[1];
  var ml = React.useState(false);
  var machinesLoading = ml[0],
    setMachinesLoading = ml[1];
  var rfs = React.useState({ error: null, stale: false, lastSuccessful: null });
  var runnerFetchState = rfs[0],
    setRunnerFetchState = rfs[1];
  var mfs = React.useState({ error: null, stale: false, lastSuccessful: null });
  var machineFetchState = mfs[0],
    setMachineFetchState = mfs[1];
  var sjs = React.useState({});
  var scheduledJobs = sjs[0],
    setScheduledJobs = sjs[1];
  // Loading flag retained only as a setter: the legacy toolstrip badge still
  // reads `scheduledJobs` (the count) but no longer renders a loading state —
  // the Schedules tab body and its loading UI now live in pages/ScheduledJobs.
  var sjl = React.useState(false);
  var setScheduledJobsLoading = sjl[1];
  var las = React.useState({});
  var localApps = las[0],
    setLocalApps = las[1];
  var lal = React.useState(false);
  var localAppsLoading = lal[0],
    setLocalAppsLoading = lal[1];
  var rcs = React.useState(null);
  var runnerCapacity = rcs[0],
    setRunnerCapacity = rcs[1];
  var rcl = React.useState(false);
  var runnerCapacityLoading = rcl[0],
    setRunnerCapacityLoading = rcl[1];
  var ds = React.useState({});
  var deployment = ds[0],
    setDeployment = ds[1];
  var dss = React.useState({});
  var deploymentState = dss[0],
    setDeploymentState = dss[1];
  var dsl = React.useState(false);
  var deploymentStateLoading = dsl[0],
    setDeploymentStateLoading = dsl[1];
  var arcs = React.useState({});
  var remediationConfig = arcs[0],
    setRemediationConfig = arcs[1];
  var arws = React.useState({});
  var remediationWorkflows = arws[0],
    setRemediationWorkflows = arws[1];
  var arl = React.useState(false);
  var remediationLoading = arl[0],
    setRemediationLoading = arl[1];
  var are = React.useState(null);
  var remediationError = are[0],
    setRemediationError = are[1];
  var arp = React.useState("jules_api");
  var remediationProvider = arp[0],
    setRemediationProvider = arp[1];
  var arm = React.useState("");
  var remediationModel = arm[0],
    setRemediationModel = arm[1];
  var arps = React.useState(null);
  var remediationPlan = arps[0],
    setRemediationPlan = arps[1];
  var ards = React.useState(null);
  var remediationDispatchState = ards[0],
    setRemediationDispatchState = ards[1];
  var arrs = React.useState("");
  var remediationSelectedRunId = arrs[0],
    setRemediationSelectedRunId = arrs[1];
  var rhs = React.useState([]);
  var remediationHistory = rhs[0],
    setRemediationHistory = rhs[1];
  var wts = React.useState([]);
  var workflowsList = wts[0],
    setWorkflowsList = wts[1];
  var wtl = React.useState(false);
  var workflowsListLoading = wtl[0],
    setWorkflowsListLoading = wtl[1];
  var wte = React.useState(null);
  var workflowsListError = wte[0],
    setWorkflowsListError = wte[1];
  var crs = React.useState({ probes: [], summary: {} });
  var credentialsData = crs[0],
    setCredentialsData = crs[1];
  var crl = React.useState(false);
  var credentialsLoading = crl[0],
    setCredentialsLoading = crl[1];
  var cre = React.useState(null);
  var credentialsError = cre[0],
    setCredentialsError = cre[1];
  var fos = React.useState({});
  var fleetOrchData = fos[0],
    setFleetOrchData = fos[1];
  var fol = React.useState(false);
  var fleetOrchLoading = fol[0],
    setFleetOrchLoading = fol[1];
  var foe = React.useState(null);
  var fleetOrchError = foe[0],
    setFleetOrchError = foe[1];

  var acs = React.useState([]);
  var assessmentScores = acs[0],
    setAssessmentScores = acs[1];
  var acl = React.useState(false);
  var assessmentLoading = acl[0],
    setAssessmentLoading = acl[1];
  var ace = React.useState(null);
  var assessmentError = ace[0],
    setAssessmentError = ace[1];
  var frs2 = React.useState([]);
  var featureRequests = frs2[0],
    setFeatureRequests = frs2[1];
  var frt = React.useState([]);
  var promptTemplates = frt[0],
    setPromptTemplates = frt[1];
  var frstds = React.useState({});
  var featureStandards = frstds[0],
    setFeatureStandards = frstds[1];
  var frl = React.useState(false);
  var featureRequestsLoading = frl[0],
    setFeatureRequestsLoading = frl[1];
  var pns = React.useState({ notes: "", enabled: true });
  var promptNotes = pns[0],
    setPromptNotes = pns[1];
  var asstS = React.useState(lsGet(ASST_LS.open, lsGet(ASST_LS.openByDefault, false)));
  var asstOpen = asstS[0], setAsstOpen = asstS[1];
  function toggleAsst() { setAsstOpen(function (o) { var n = !o; lsSet(ASST_LS.open, n); return n; }); }
  var rmS = React.useState(null);
  var recoveryModal = rmS[0], setRecoveryModal = rmS[1];
  var seS = React.useState(false);
  var sessionExpiredOpen = seS[0], setSessionExpiredOpen = seS[1];
  var rauS = React.useState({ violations: [], last_checked: null, error: null });
  var runnerAudit = rauS[0], setRunnerAudit = rauS[1];
  // The standalone billing/audit dismiss state was removed in issue #819 — the
  // alert now lives in the consolidated AlertsCenter with durable ack/snooze.

  React.useEffect(function () {
    var unsubscribe = subscribeSessionExpired(function () {
      setSessionExpiredOpen(true);
    });
    window._showAuthError = function () {
      setSessionExpiredOpen(true);
    };
    return function () {
      unsubscribe();
      if (window._showAuthError) delete window._showAuthError;
    };
  }, []);

  React.useEffect(function () {
    return installWheelValueGuard(document);
  }, []);

  React.useEffect(function () {
    if (!window.matchMedia) return;
    var media = window.matchMedia("(max-width: 768px)");
    function onMobileCredentialViewportChange(event) {
      setMobileCredentialsViewport(event.matches);
    }
    setMobileCredentialsViewport(media.matches);
    if (media.addEventListener) {
      media.addEventListener("change", onMobileCredentialViewportChange);
      return function () {
        media.removeEventListener("change", onMobileCredentialViewportChange);
      };
    }
    media.addListener(onMobileCredentialViewportChange);
    return function () {
      media.removeListener(onMobileCredentialViewportChange);
    };
  }, []);

  function fetchFleetOrchestration() {
    setFleetOrchLoading(true);
    legacyFetch("/api/fleet/orchestration")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setFleetOrchData(d || {});
        setFleetOrchError(null);
        setFleetOrchLoading(false);
      })
      .catch(function () {
        setFleetOrchError("Failed to load fleet orchestration data.");
        setFleetOrchLoading(false);
      });
  }

  function orchDispatch(params) {
    return legacyFetch("/api/fleet/orchestration/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Dispatch failed");
        return d;
      });
    });
  }

  function orchDeploy(params) {
    return legacyFetch("/api/fleet/orchestration/deploy", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Deploy failed");
        return d;
      });
    });
  }

  function fetchCredentials() {
    setCredentialsLoading(true);
    legacyFetch("/api/credentials")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setCredentialsData(d || {});
        setCredentialsError(null);
        setCredentialsLoading(false);
      })
        .catch(function () {
          setCredentialsError("Failed to probe credentials.");
          setCredentialsLoading(false);
        });
  }

  function setCredentialKey(probe, keyValue) {
    var provider = probe && probe.key_provider;
    if (!provider) return;
    var providerLabel = probe.label || probe.name || provider;
    if (!keyValue) return;
    if (!keyValue) return;
    legacyFetch("/api/credentials/set-key", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({
        provider: provider,
        key: keyValue,
        restart_maxwell: false,
      }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error((data && data.detail) || ("HTTP " + r.status));
          return data;
        });
      })
      .then(function () {
        fetchCredentials();
      })
      .catch(function (err) {
        setCredentialsError(err.message || "Failed to save key.");
      });
  }

  function fetchWorkflowsList() {
    setWorkflowsListLoading(true);
    legacyFetch("/api/workflows/list")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setWorkflowsList((d && d.workflows) || []);
        setWorkflowsListError(null);
        setWorkflowsListLoading(false);
      })
      .catch(function () {
        setWorkflowsListError("Failed to load workflows list.");
        setWorkflowsListLoading(false);
      });
  }
  function dispatchWorkflow(params) {
    return legacyFetch("/api/workflows/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Dispatch failed");
        return d;
      });
    });
  }
  var mxs = React.useState({});
  var maxwellStatus = mxs[0],
    setMaxwellStatus = mxs[1];
  var mxl = React.useState(false);
  var maxwellLoading = mxl[0],
    setMaxwellLoading = mxl[1];
  var mxe = React.useState(null);
  var maxwellError = mxe[0],
    setMaxwellError = mxe[1];

  function fetchMaxwellStatus() {
    setMaxwellLoading(true);
    legacyFetch("/api/maxwell/status")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        setMaxwellStatus(d || {});
        setMaxwellError(null);
        setMaxwellLoading(false);
      })
      .catch(function () {
        setMaxwellError("Failed to probe Maxwell status.");
        setMaxwellLoading(false);
      });
  }
  function maxwellControl(params) {
    return legacyFetch("/api/maxwell/control", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.detail || "Control failed");
        return d;
      });
    });
  }

  function fetchOptionalStats() {
    legacyFetch("/api/stats")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setStats(d);
      })
      .catch(function () {});
  }
  function fetchFleet() {
    fetchOptionalStats();
    Promise.all([
      legacyFetch("/api/runners")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/runs?per_page=30")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/system")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/fleet/schedule")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/github/status")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
    ])
      .then(function (r) {
        if (r[0] && r[0].runners) {
          setRunners(r[0].runners);
          setRunnerFetchState({
            error: r[0].error || null,
            stale: !!(r[0].stale || r[0].degraded),
            lastSuccessful: new Date().toISOString(),
          });
          setConnected(!r[0].error);
        } else {
          setRunnerFetchState({
            error: "Runner status unavailable; retaining last known data.",
            stale: runners.length > 0,
            lastSuccessful: runnerFetchState.lastSuccessful,
          });
          setConnected(false);
        }
        if (r[1] && r[1].workflow_runs) {
          setRuns(r[1].workflow_runs);
        }
        if (r[2] && r[2].hostname) {
          setSystem(r[2]);
        }
        if (r[3]) {
          setRunnerCapacity(r[3]);
        }
        if (r[4]) {
          setGithubStatus(r[4]);
        }
        setLastUpdate(new Date().toLocaleTimeString());
      })
      .catch(function () {
        setConnected(false);
      });
  }
  function fetchRepos() {
    setReposLoading(true);
    legacyFetch("/api/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setRepos(d.repos);
        setReposLoading(false);
      })
      .catch(function () {
        setReposLoading(false);
      });
  }
  function fetchTests() {
    setTestsLoading(true);
    legacyFetch("/api/heavy-tests/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setTestRepos(d.repos);
        setTestsLoading(false);
      })
      .catch(function () {
        setTestsLoading(false);
      });
  }
  function fetchCiResults() {
    legacyFetch("/api/tests/ci-results")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.results) setCiResults(d.results);
      })
      .catch(function () {});
  }
  function fetchReports() {
    setReportsLoading(true);
    legacyFetch("/api/reports")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.reports) setReports(d.reports);
        setReportsLoading(false);
      })
      .catch(function () {
        setReportsLoading(false);
      });
  }
  function fetchQueue() {
    setQueueLoading(true);
    legacyFetch("/api/queue")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setQueue(d);
        setQueueLoading(false);
      })
      .catch(function () {
        setQueueLoading(false);
      });
  }
  function fetchMachines() {
    setMachinesLoading(true);
    legacyFetch("/api/fleet/nodes")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) {
          setMachinesData(d);
          setMachineFetchState({
            error: d.fleet_probe_error || null,
            stale: !!(d.partial || d.degraded),
            lastSuccessful: new Date().toISOString(),
          });
        } else {
          setMachineFetchState({
            error: "Machine health unavailable; retaining last known data.",
            stale: (machinesData.nodes || []).length > 0,
            lastSuccessful: machineFetchState.lastSuccessful,
          });
        }
        setMachinesLoading(false);
      })
      .catch(function () {
        setMachineFetchState({
          error: "Machine health unavailable; retaining last known data.",
          stale: (machinesData.nodes || []).length > 0,
          lastSuccessful: machineFetchState.lastSuccessful,
        });
        setMachinesLoading(false);
      });
  }
  function fetchLocalApps() {
    setLocalAppsLoading(true);
    legacyFetch("/api/local-apps")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setLocalApps(d);
        setLocalAppsLoading(false);
      })
      .catch(function () {
        setLocalAppsLoading(false);
      });
  }
  function fetchEnrichedRuns() {
    legacyFetch("/api/runs/enriched?per_page=50")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.workflow_runs) setEnrichedRuns(d.workflow_runs);
      })
      .catch(function () {});
  }
  function fetchWatchdog() {
    legacyFetch("/api/watchdog")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setWatchdog(d);
      })
      .catch(function () {});
  }
  function fetchDeployment() {
    legacyFetch("/api/deployment")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeployment(d);
      })
      .catch(function () {});
  }
  function fetchDeploymentState() {
    setDeploymentStateLoading(true);
    legacyFetch("/api/deployment/state")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeploymentState(d);
        setDeploymentStateLoading(false);
      })
      .catch(function () {
        setDeploymentStateLoading(false);
      });
  }
  function fetchRemediationConfig() {
    setRemediationLoading(true);
    Promise.all([
      legacyFetch("/api/agent-remediation/config").then(function (r) {
        return r.json();
      }),
      legacyFetch("/api/agent-remediation/workflows").then(function (r) {
        return r.json();
      }),
      legacyFetch("/api/agent-remediation/history")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { history: [] };
        }),
    ])
      .then(function (data) {
        setRemediationConfig(data[0] || {});
        setRemediationWorkflows(data[1] || {});
        setRemediationHistory((data[2] && data[2].history) || []);
        setRemediationProvider(
          (data[0] &&
            data[0].policy &&
            data[0].policy.default_provider) ||
            "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function () {
        setRemediationError(
          "Failed to load remediation controls from the dashboard backend.",
        );
        setRemediationLoading(false);
      });
  }
  function saveRemediationConfig(policy) {
    setRemediationLoading(true);
    return legacyFetch("/api/agent-remediation/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ policy: policy }),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Save failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationConfig(d || {});
        setRemediationProvider(
          (d && d.policy && d.policy.default_provider) || "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
        return d;
      })
      .catch(function (e) {
        setRemediationError(e.message || "Save failed");
        setRemediationLoading(false);
        throw e;
      });
  }
  function buildRemediationContext(run) {
    if (!run) return null;
    var branch = run.head_branch || "";
    var repoName =
      run.repository && run.repository.name ? run.repository.name : "";
    var workflowName = run.name || run.workflow_name || "CI Standard";
    return {
      repository: repoName,
      workflow_name: workflowName,
      branch: branch,
      run_id: run.id,
      failure_reason:
        workflowName + " failed for " + repoName + " on " + branch,
      protected_branch: branch === "main" || branch === "master",
      attempts: [],
    };
  }
  function previewRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before previewing remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState(null);
    legacyFetch("/api/agent-remediation/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider_override: remediationProvider,
          model_override: remediationModel || undefined,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Preview failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationPlan(d);
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function (e) {
        setRemediationPlan(null);
        setRemediationError(e.message || "Preview failed");
        setRemediationLoading(false);
      });
  }
  function dispatchRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before dispatching remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState({
      note:
        "Dispatch submitted for " +
        payload.repository +
        " #" +
        payload.run_id +
        ". Waiting for agent heartbeat.",
    });
    legacyFetch("/api/agent-remediation/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider: remediationProvider,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Dispatch failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationDispatchState({
          note:
            "Dispatched " + d.provider + " through " + d.workflow + ".",
        });
        setRemediationError(null);
        setRemediationLoading(false);
        // Refresh history after dispatch
        legacyFetch("/api/agent-remediation/history")
          .then(function (r) {
            return r.json();
          })
          .then(function (hd) {
            if (hd && hd.history) setRemediationHistory(hd.history);
          })
          .catch(function () {});
      })
      .catch(function (e) {
        setRemediationDispatchState({
          error: e.message || "Dispatch failed",
        });
        setRemediationError(e.message || "Dispatch failed");
        setRemediationLoading(false);
      });
  }
  function fetchScheduledJobs() {
    setScheduledJobsLoading(true);
    legacyFetch("/api/scheduled-workflows")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setScheduledJobs(d);
        setScheduledJobsLoading(false);
      })
      .catch(function () {
        setScheduledJobsLoading(false);
      });
  }
  function fetchRunnerCapacity() {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }
  function saveRunnerCapacity(schedule, apply) {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ schedule: schedule, apply: apply }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("save failed");
        return r.json();
      })
      .then(function (d) {
        setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
        setTimeout(fetchFleet, 2000);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }


  function fetchAssessments() {
    setAssessmentLoading(true);
    setAssessmentError(null);
    legacyFetch("/api/assessments/scores")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setAssessmentScores(d.scores || []);
        setAssessmentLoading(false);
      })
      .catch(function () {
        setAssessmentError("Failed to load assessment scores");
        setAssessmentLoading(false);
      });
  }

  function dispatchAssessment(params) {
    return legacyFetch("/api/assessments/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.detail || "dispatch failed");
        });
      return r.json();
    });
  }

  function fetchFeatureRequests() {
    setFeatureRequestsLoading(true);
    Promise.all([
      legacyFetch("/api/feature-requests")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { requests: [] };
        }),
      legacyFetch("/api/feature-requests/templates")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return { templates: [], promptNotes: { notes: "", enabled: true } };
        }),
    ]).then(function (results) {
      setFeatureRequests(results[0].requests || []);
      setPromptTemplates(results[1].templates || []);
      if (results[1].promptNotes) {
        setPromptNotes(results[1].promptNotes);
      }
      setFeatureRequestsLoading(false);
    });
  }

  function dispatchFeatureRequest(params) {
    return legacyFetch("/api/feature-requests/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.detail || "dispatch failed");
        });
      return r.json();
    });
  }

  function savePromptTemplate(params) {
    return legacyFetch("/api/prompt-templates", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(params),
    })
      .then(function (r) {
        if (!r.ok)
          return r.json().then(function (e) {
            throw new Error(e.detail || "save failed");
          });
        return r.json();
      })
      .then(function (d) {
        fetchFeatureRequests();
        return d;
      });
  }

  function updatePromptNotes(params) {
    return legacyFetch("/api/settings/prompt-notes", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    }).then(function (r) {
      if (!r.ok)
        return r.json().then(function (e) {
          throw new Error(e.detail || "save failed");
        });
      return r.json();
    });
  }

  function fetchOptionalStats() {
    legacyFetch("/api/stats")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setStats(d);
      })
      .catch(function () {});
  }
  function fetchFleet() {
    fetchOptionalStats();
    Promise.all([
      legacyFetch("/api/runners")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/runs?per_page=30")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/system")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
      legacyFetch("/api/fleet/schedule")
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return null;
        }),
    ])
      .then(function (r) {
        if (r[0] && r[0].runners) {
          setRunners(r[0].runners);
          setRunnerFetchState({
            error: r[0].error || null,
            stale: !!(r[0].stale || r[0].degraded),
            lastSuccessful: new Date().toISOString(),
          });
          setConnected(!r[0].error);
        } else {
          setRunnerFetchState({
            error: "Runner status unavailable; retaining last known data.",
            stale: runners.length > 0,
            lastSuccessful: runnerFetchState.lastSuccessful,
          });
          setConnected(false);
        }
        if (r[1] && r[1].workflow_runs) {
          setRuns(r[1].workflow_runs);
        }
        if (r[2] && r[2].hostname) {
          setSystem(r[2]);
        }
        if (r[3]) {
          setRunnerCapacity(r[3]);
        }
        setLastUpdate(new Date().toLocaleTimeString());
      })
      .catch(function () {
        setConnected(false);
      });
  }
  function fetchRepos() {
    setReposLoading(true);
    legacyFetch("/api/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setRepos(d.repos);
        setReposLoading(false);
      })
      .catch(function () {
        setReposLoading(false);
      });
  }
  function fetchTests() {
    setTestsLoading(true);
    legacyFetch("/api/heavy-tests/repos")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.repos) setTestRepos(d.repos);
        setTestsLoading(false);
      })
      .catch(function () {
        setTestsLoading(false);
      });
  }
  function fetchCiResults() {
    legacyFetch("/api/tests/ci-results")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.results) setCiResults(d.results);
      })
      .catch(function () {});
  }
  function fetchReports() {
    setReportsLoading(true);
    legacyFetch("/api/reports")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.reports) setReports(d.reports);
        setReportsLoading(false);
      })
      .catch(function () {
        setReportsLoading(false);
      });
  }
  function fetchQueue() {
    setQueueLoading(true);
    legacyFetch("/api/queue")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setQueue(d);
        setQueueLoading(false);
      })
      .catch(function () {
        setQueueLoading(false);
      });
  }
  function fetchMachines() {
    setMachinesLoading(true);
    legacyFetch("/api/fleet/nodes")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) {
          setMachinesData(d);
          setMachineFetchState({
            error: d.fleet_probe_error || null,
            stale: !!(d.partial || d.degraded),
            lastSuccessful: new Date().toISOString(),
          });
        } else {
          setMachineFetchState({
            error: "Machine health unavailable; retaining last known data.",
            stale: (machinesData.nodes || []).length > 0,
            lastSuccessful: machineFetchState.lastSuccessful,
          });
        }
        setMachinesLoading(false);
      })
      .catch(function () {
        setMachineFetchState({
          error: "Machine health unavailable; retaining last known data.",
          stale: (machinesData.nodes || []).length > 0,
          lastSuccessful: machineFetchState.lastSuccessful,
        });
        setMachinesLoading(false);
      });
  }
  function fetchLocalApps() {
    setLocalAppsLoading(true);
    legacyFetch("/api/local-apps")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setLocalApps(d);
        setLocalAppsLoading(false);
      })
      .catch(function () {
        setLocalAppsLoading(false);
      });
  }
  function fetchEnrichedRuns() {
    legacyFetch("/api/runs/enriched?per_page=50")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d && d.workflow_runs) setEnrichedRuns(d.workflow_runs);
      })
      .catch(function () {});
  }
  function fetchWatchdog() {
    legacyFetch("/api/watchdog")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setWatchdog(d);
      })
      .catch(function () {});
  }
  function fetchDeployment() {
    legacyFetch("/api/deployment")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeployment(d);
      })
      .catch(function () {});
  }
  function fetchDeploymentState() {
    setDeploymentStateLoading(true);
    legacyFetch("/api/deployment/state")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setDeploymentState(d);
        setDeploymentStateLoading(false);
      })
      .catch(function () {
        setDeploymentStateLoading(false);
      });
  }
  function fetchRemediationConfig() {
    setRemediationLoading(true);
    Promise.all([
      legacyFetch("/api/agent-remediation/config").then(function (r) {
        return r.json();
      }),
      legacyFetch("/api/agent-remediation/workflows").then(function (r) {
        return r.json();
      }),
    ])
      .then(function (data) {
        setRemediationConfig(data[0] || {});
        setRemediationWorkflows(data[1] || {});
        setRemediationProvider(
          (data[0] &&
            data[0].policy &&
            data[0].policy.default_provider) ||
            "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function () {
        setRemediationError(
          "Failed to load remediation controls from the dashboard backend.",
        );
        setRemediationLoading(false);
      });
  }
  function saveRemediationConfig(policy) {
    setRemediationLoading(true);
    return legacyFetch("/api/agent-remediation/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ policy: policy }),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Save failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationConfig(d || {});
        setRemediationProvider(
          (d && d.policy && d.policy.default_provider) || "jules_api",
        );
        setRemediationError(null);
        setRemediationLoading(false);
        return d;
      })
      .catch(function (e) {
        setRemediationError(e.message || "Save failed");
        setRemediationLoading(false);
        throw e;
      });
  }
  function buildRemediationContext(run) {
    if (!run) return null;
    var branch = run.head_branch || "";
    var repoName = run.repository && run.repository.name
      ? run.repository.name
      : "";
    var workflowName = run.name || run.workflow_name || "CI Standard";
    return {
      repository: repoName,
      workflow_name: workflowName,
      branch: branch,
      run_id: run.id,
      failure_reason:
        workflowName +
        " failed for " +
        repoName +
        " on " +
        branch,
      protected_branch: branch === "main" || branch === "master",
      attempts: [],
    };
  }
  function previewRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before previewing remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState(null);
    legacyFetch("/api/agent-remediation/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider_override: remediationProvider,
          model_override: remediationModel || undefined,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Preview failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationPlan(d);
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function (e) {
        setRemediationPlan(null);
        setRemediationError(e.message || "Preview failed");
        setRemediationLoading(false);
      });
  }
  function dispatchRemediation(run) {
    var payload = buildRemediationContext(run);
    if (!payload) {
      setRemediationError(
        "Select a failed run before dispatching remediation.",
      );
      return;
    }
    setRemediationLoading(true);
    setRemediationDispatchState({
      note:
        "Dispatch submitted for " +
        payload.repository +
        " #" +
        payload.run_id +
        ". Waiting for agent heartbeat.",
    });
    legacyFetch("/api/agent-remediation/dispatch", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify(
        Object.assign({}, payload, {
          provider: remediationProvider,
        }),
      ),
    })
      .then(function (r) {
        return r.json().then(function (d) {
          if (!r.ok) throw new Error(d.detail || "Dispatch failed");
          return d;
        });
      })
      .then(function (d) {
        setRemediationDispatchState({
          note:
            "Dispatched " + d.provider + " through " + d.workflow + ".",
        });
        setRemediationError(null);
        setRemediationLoading(false);
      })
      .catch(function (e) {
        setRemediationDispatchState({
          error: e.message || "Dispatch failed",
        });
        setRemediationError(e.message || "Dispatch failed");
        setRemediationLoading(false);
      });
  }
  function fetchScheduledJobs() {
    setScheduledJobsLoading(true);
    legacyFetch("/api/scheduled-workflows")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setScheduledJobs(d);
        setScheduledJobsLoading(false);
      })
      .catch(function () {
        setScheduledJobsLoading(false);
      });
  }
  function fetchRunnerCapacity() {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d) setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }
  function saveRunnerCapacity(schedule, apply) {
    setRunnerCapacityLoading(true);
    legacyFetch("/api/fleet/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
      body: JSON.stringify({ schedule: schedule, apply: apply }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("save failed");
        return r.json();
      })
      .then(function (d) {
        setRunnerCapacity(d);
        setRunnerCapacityLoading(false);
        setTimeout(fetchFleet, 2000);
      })
      .catch(function () {
        setRunnerCapacityLoading(false);
      });
  }

  function fetchPrincipal() {
    legacyFetch("/api/auth/me")
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(d) { setPrincipal(d); })
      .catch(function() { setPrincipal(null); });
  }

  function fetchRunnerAudit() {
    legacyFetch("/api/runner-routing-audit")
      .then(function(r) { return r.json(); })
      .then(function(d) {
        // NOTE (issue #819): we intentionally do NOT reset any dismissal here.
        // The old `setAuditBannerDismissed(false)` ran on every poll and
        // structurally undid the operator's Dismiss. Acknowledgement is now
        // durable via lib/alertAck (keyed by alert id + contentHash), so a
        // re-poll never re-surfaces an acknowledged alert unless its content
        // materially changes.
        setRunnerAudit(d || { violations: [], last_checked: null, error: null });
      })
      .catch(function() {});
  }

  function triggerRunnerAuditRefresh() {
    legacyFetch("/api/runner-routing-audit/refresh", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function() { setTimeout(fetchRunnerAudit, 3000); })
      .catch(function() {});
  }

  React.useEffect(function () {
    fetchPrincipal();
    fetchFleet();
    fetchRepos();
    fetchTests();
    fetchCiResults();
    fetchReports();
    fetchQueue();
    fetchMachines();
    fetchLocalApps();
    fetchEnrichedRuns();
    fetchWatchdog();
    fetchDeployment();
    fetchDeploymentState();
    fetchScheduledJobs();
    fetchRunnerCapacity();
    fetchRunnerAudit();
    var cleanupIntervals = [
      createVisibleInterval(fetchFleet, 30000),
      createVisibleInterval(fetchRepos, 120000),
      createVisibleInterval(fetchTests, 120000),
      createVisibleInterval(fetchCiResults, 120000),
      createVisibleInterval(fetchReports, 300000),
      createVisibleInterval(fetchQueue, 60000),
      createVisibleInterval(fetchMachines, 60000),
      createVisibleInterval(fetchEnrichedRuns, 60000),
      createVisibleInterval(fetchWatchdog, 120000),
      createVisibleInterval(fetchScheduledJobs, 300000),
      createVisibleInterval(fetchLocalApps, 90000),
      createVisibleInterval(fetchRunnerCapacity, 60000),
      createVisibleInterval(fetchDeployment, 300000),
      createVisibleInterval(fetchDeploymentState, 300000),
      createVisibleInterval(fetchRunnerAudit, 300000),
    ];
    return function () {
      cleanupIntervals.forEach(function (cleanup) { cleanup(); });
    };
  }, []);

  React.useEffect(function () {
    var failureCount = 0;
    var maxFailures = 3;
    function checkHealth() {
      legacyFetch("/health", { method: "GET" })
        .then(function (r) {
          if (r.ok) {
            failureCount = 0;
            setRecoveryModal(null);
          } else {
            failureCount++;
            if (failureCount >= maxFailures) {
              setRecoveryModal({ visible: true });
            }
          }
        })
        .catch(function () {
          failureCount++;
          if (failureCount >= maxFailures) {
            setRecoveryModal({ visible: true });
          }
        });
    }
    var cleanupHealthInterval = createVisibleInterval(checkHealth, 2000);
    return function () { cleanupHealthInterval(); };
  }, []);

  function onFleet(a) {
    setActionLoading(true);
    legacyFetch("/api/fleet/control/" + a, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function () {
        setTimeout(fetchFleet, 2000);
      })
      .finally(function () {
        setTimeout(function () {
          setActionLoading(false);
        }, 2500);
      });
  }
  function onRunner(id, a) {
    setActionLoading(true);
    legacyFetch("/api/runners/" + id + "/" + a, { method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function () {
        setTimeout(fetchFleet, 2000);
      })
      .finally(function () {
        setTimeout(function () {
          setActionLoading(false);
        }, 2500);
      });
  }

  var asstPosition = lsGet(ASST_LS.position, "right");

  // Roll the cross-cutting fleet signals into ONE durable, acknowledgeable
  // surface (status pill + drawer) instead of the old hero list + three sticky
  // banners that re-popped on every poll. Uses the same pure rollup as the
  // FleetTab hero so the two stay in sync (DRY). Telemetry-degraded and GitHub
  // API conditions are folded in as additional synthetic alerts so a single
  // surface owns every cross-cutting signal.
  var alertNodes = (machinesData.nodes || []);
  var alertMachineCount = alertNodes.length;
  var alertMachineOnline = alertNodes.filter(function (n) { return n.online; }).length;
  var appAlertsResult = fleetAlerts.computeFleetAlerts({
    machineCount: alertMachineCount,
    machineOnline: alertMachineOnline,
    machineNodes: alertNodes,
    watchdog: watchdog,
    stats: stats,
    completedRuns: stats.runs_completed || 0,
    runnerAudit: runnerAudit,
  });
  var appAlerts = appAlertsResult.alerts.slice();
  // Fold the former "telemetry degraded" sticky banner into the surface.
  if (runnerFetchState.error || runnerFetchState.stale || machineFetchState.error || machineFetchState.stale) {
    var degradedDetail = [
      runnerFetchState.error
        ? "Runner status is unavailable; showing last known or degraded data."
        : runnerFetchState.stale ? "Runner status is stale." : null,
      machineFetchState.error
        ? "Machine health is partially unavailable; local data is kept when available."
        : machineFetchState.stale ? "Machine health is partial." : null,
    ].filter(Boolean).join(" ");
    appAlerts.push({
      id: "telemetry-degraded",
      level: "warning",
      title: "Fleet telemetry degraded",
      detail: degradedDetail || "Some fleet telemetry is unavailable.",
      contentHash: fleetAlerts.alertContentHash({
        id: "telemetry-degraded", level: "warning",
        title: "Fleet telemetry degraded", detail: degradedDetail,
      }),
    });
  }
  // Fold the former "GitHub API degraded" sticky banner into the surface.
  if (githubStatus && (githubStatus.status === "rate_limited" || githubStatus.status === "auth_error")) {
    var ghDetail = githubStatus.status === "rate_limited"
      ? ("Rate limited" + (githubStatus.retry_after_seconds ? " for about " + githubStatus.retry_after_seconds + "s" : "") + ". Cached local runner data may still be shown.")
      : "Authentication failed. Refresh the dashboard GitHub token before relying on GitHub-backed views.";
    appAlerts.push({
      id: "github-api",
      level: githubStatus.status === "auth_error" ? "critical" : "warning",
      title: "GitHub API degraded",
      detail: ghDetail,
      contentHash: fleetAlerts.alertContentHash({
        id: "github-api",
        level: githubStatus.status === "auth_error" ? "critical" : "warning",
        title: "GitHub API degraded", detail: ghDetail,
      }),
    });
  }
  // Fold the event-derived alerts (disk-pressure, runners-offline) into the
  // consolidated surface (issue #863). De-dup by id so a rollup machines-offline
  // and an event runners-offline don't both show; the event alert carries the
  // richer "disk pressure" reason, so it wins where ids differ.
  (fleetEvents.alerts || []).forEach(function (ea) {
    if (!appAlerts.some(function (a) { return a.id === ea.id; })) {
      appAlerts.push(ea);
    }
  });
  function onAlertNavigate(alertId) {
    if (alertId === "hosted-runners") { setTab("runner-audit"); return; }
    if (alertId === "github-api") { setTab("runner-audit"); return; }
    if (alertId === "machines-offline" || alertId === "telemetry-degraded") { setTab("machines"); return; }
    if (alertId === "disk-pressure" || alertId === "runners-offline") { setTab("events"); return; }
    setTab("overview");
  }

  return h(
    "div",
    null,
    // In chromeless mode the new desktop shell (#802) supplies the navigation
    // chrome (sidebar + slim toolstrip), so App suppresses its own legacy
    // header/toolstrip and renders only the page body below.
    chromeless ? null : h(
      "header",
      { className: "app-header app-header--rows" },
      h(
        "div",
        { className: "app-header__row app-header__row--primary" },
      h(
        "div",
        { className: "app-logo" },
        h(
          "svg",
          { width: 28, height: 28, viewBox: "0 0 32 32" },
          h(
            "defs",
            null,
            h(
              "linearGradient",
              { id: "lg", x1: 0, y1: 0, x2: 1, y2: 1 },
              h("stop", { offset: "0%", stopColor: "var(--accent-blue)" }),
              h("stop", { offset: "100%", stopColor: "var(--accent-purple)" }),
            ),
          ),
          h("rect", { width: 32, height: 32, rx: 6, fill: "url(#lg)" }),
          h("path", { d: "M18 6l-7 13h6l-1 7 7-13h-6z", fill: "white" }),
        ),
        h("span", { className: "logo-text" }, "Dashboard"),
      ),
      h(
        "div",
        { className: "tab-bar", role: "tablist", "aria-label": "Dashboard sections" },
        h(
          "button",
          {
            className: "tab-btn" + (tab === "overview" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "overview",
            onClick: function () {
              setTab("overview");
            },
          },
          I.server(14),
          "Overview",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "remediation" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "remediation",
            onClick: function () {
              setTab("remediation");
              fetchRemediationConfig();
            },
          },
          I.issue(14),
          "Remediation",
          runs.filter(function (run) {
            return run.conclusion === "failure";
          }).length > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--danger section-badge--offset",
                },
                runs.filter(function (run) {
                  return run.conclusion === "failure";
                }).length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "agent-dispatch" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "agent-dispatch",
            onClick: function () {
              setTab("agent-dispatch");
            },
          },
          I.issue(14),
          "Dispatch",
          runs.filter(function (run) {
            return run.conclusion === "failure";
          }).length > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--danger section-badge--offset",
                },
                runs.filter(function (run) {
                  return run.conclusion === "failure";
                }).length,
              )
            : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "queue" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "queue",
            onClick: function () {
              setTab("queue");
              fetchQueue();
            },
          },
          I.queue(14),
          "Queue",
          (queue.total || 0) > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--info section-badge--offset",
                },
                queue.total || 0,
              )
            : null,
        ),
        conductorEnabled
          ? h(
              "button",
              {
                className: "tab-btn" + (tab === "conductor" ? " active" : ""),
                role: "tab",
                "aria-selected": tab === "conductor",
                onClick: function () {
                  setTab("conductor");
                },
              },
              I.queue(14),
              "Conductor",
            )
          : null,
        h(
          "button",
          {
            className: "tab-btn" + (tab === "machines" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "machines",
            onClick: function () {
              setTab("machines");
              fetchMachines();
            },
          },
          I.server(14),
          "Machines",
          (machinesData.count || 0) > 1
            ? h(
                "span",
                {
                  className: "section-badge section-badge--success section-badge--offset",
                },
                machinesData.count || 0,
              )
            : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "org" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "org",
            onClick: function () {
              setTab("org");
            },
          },
          I.repo(14),
          "Organization",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "tests" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "tests",
            onClick: function () {
              setTab("tests");
            },
          },
          I.flask(14),
          "Tests",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (isAnalysisTabKey(tab) ? " active" : ""),
            role: "tab",
            "aria-selected": isAnalysisTabKey(tab),
            onClick: function () {
              setTab("analysis");
              fetchEnrichedRuns();
              fetchReports();
            },
          },
          I.activity(14),
          "Analysis",
          enrichedRuns.length > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--purple section-badge--offset",
                },
                enrichedRuns.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "scheduled-jobs" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "scheduled-jobs",
            onClick: function () {
              setTab("scheduled-jobs");
              fetchScheduledJobs();
            },
          },
          I.clock(14),
          "Schedules",
          (scheduledJobs.scheduled_workflow_count || 0) > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--info section-badge--offset",
                },
                scheduledJobs.scheduled_workflow_count,
              )
            : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "workflows" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "workflows",
            onClick: function () {
              setTab("workflows");
              fetchWorkflowsList();
            },
          },
          I.activity(14),
          "Workflows",
          workflowsList.length > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--info section-badge--offset",
                },
                workflowsList.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "runner-schedule" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "runner-schedule",
            onClick: function () {
              setTab("runner-schedule");
              fetchRunnerCapacity();
            },
          },
          I.clock(14),
          "Runner Plan",
          runnerCapacity && runnerCapacity.state
            ? h(
                "span",
                {
                  className: "section-badge section-badge--success section-badge--offset",
                },
                runnerCapacity.state.desired || 0,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "deployment" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "deployment",
            onClick: function () {
              setTab("deployment");
              fetchDeploymentState();
            },
          },
          I.server(14),
          "Deployment",
          deploymentStateLoading ||
            ((deploymentState.rollout_state || {}).machines_attention ||
              0) > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--warning section-badge--offset",
                },
                deploymentStateLoading
                  ? "\u2026"
                  : (deploymentState.rollout_state || {})
                      .machines_attention,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "local-apps" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "local-apps",
            onClick: function () {
              setTab("local-apps");
              fetchLocalApps();
            },
          },
          I.server(14),
          "Local Tools",
          (function () {
            var apps = localApps.tools || localApps.apps || [];
            var n = apps.filter(localAppNeedsAttention).length;
            return n > 0
              ? h(
                  "span",
                  {
                    className: "section-badge section-badge--warning section-badge--offset",
                  },
                  n,
                )
              : null;
          })(),
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "credentials" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "credentials",
            onClick: function () {
              setTab("credentials");
              if (!mobileCredentialsViewport) fetchCredentials();
            },
          },
          I.settings(14),
          "Credentials",
          credentialsData.summary && credentialsData.summary.not_ready > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--warning section-badge--offset",
                },
                credentialsData.summary.not_ready,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" +
              (tab === "fleet-orchestration" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "fleet-orchestration",
            onClick: function () {
              setTab("fleet-orchestration");
              fetchFleetOrchestration();
            },
          },
          I.server(14),
          "Fleet Orchestration",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "assessments" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "assessments",
            onClick: function () {
              setTab("assessments");
              fetchAssessments();
            },
          },
          I.activity(14),
          "Assessments",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "feature-requests" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "feature-requests",
            onClick: function () {
              setTab("feature-requests");
              fetchFeatureRequests();
            },
          },
          I.issue(14),
          "Feature Requests",
          featureRequests.length > 0
            ? h(
                "span",
                {
                  className: "section-badge section-badge--purple section-badge--offset",
                },
                featureRequests.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "cline-launcher" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "cline-launcher",
            onClick: function () {
              setTab("cline-launcher");
            },
          },
          I.terminal ? I.terminal(14) : I.server(14),
          "Cline Launcher",
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "maxwell" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "maxwell",
            onClick: function () {
              setTab("maxwell");
              fetchMaxwellStatus();
            },
          },
          I.server(14),
          "Maxwell",
          maxwellStatus.status === "running"
            ? h(
                "span",
                {
                  className: "section-badge section-badge--success section-badge--offset",
                },
                "on",
              )
            : maxwellStatus.status
              ? h(
                  "span",
                  {
                    className: "section-badge section-badge--danger section-badge--offset",
                  },
                  "off",
                )
              : null,
        ),
        h(
          "button",
          {
            className: "tab-btn" + (tab === "runner-audit" ? " active" : ""),
            role: "tab",
            "aria-selected": tab === "runner-audit",
            onClick: function () {
              setTab("runner-audit");
            },
            title: "Hosted-runner billing audit",
          },
          I.server(14),
          "Runner Audit",
          (runnerAudit.violations && runnerAudit.violations.length > 0)
            ? h(
                "span",
                {
                  className: "section-badge section-badge--danger section-badge--offset",
                },
                runnerAudit.violations.length,
              )
            : null,
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "diagnostics" ? " active" : ""),
            onClick: function () {
              setTab("diagnostics");
            },
          },
          I.settings(14),
          "Diagnostics",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "principals" ? " active" : ""),
            onClick: function () {
              setTab("principals");
            },
          },
          I.server(14),
          "Principals",
        ),
        h(
          "button",
          {
            className:
              "tab-btn" + (tab === "settings" ? " active" : ""),
            onClick: function () {
              setTab("settings");
            },
          },
          I.settings(14),
          "Settings",
        ),
      ),
      ), // close app-header__row--primary
      h(
        "div",
        { className: "app-header__row app-header__row--secondary" },
      h(
        "div",
        { className: "header-right" },
        // The "FLEET QUOTA" widget that used to live here was a hardcoded
        // mock (14/20). Removed because it (a) showed fake data with no
        // backing API, (b) had no defined semantics — "quota" wasn't tied
        // to runner schedule limits, busy-count, or anything real, and
        // (c) was obscuring the principal/badge/settings controls in the
        // toolstrip. If a real fleet-capacity indicator is wanted, it
        // belongs in the new shell under frontend/src/shell with a clear
        // data contract (e.g. busy_runners / scheduled_runners derived
        // from /api/fleet/nodes), not as another mock here.
        principal ? h(
          "span",
          { className: "section-badge section-badge--info" },
          "Acting as: " + principal.name
        ) : null,
        h(
          "a",
          {
            href: principal ? "#" : "/api/auth/github",
            onClick: principal ? function(e) {
              e.preventDefault();
              legacyFetch("/api/auth/logout", {method: "POST", headers: { "X-Requested-With": "XMLHttpRequest" }})
                .then(function() { window.location.reload(); });
            } : undefined,
            className: "btn legacy-toolstrip-link",
          },
          principal ? "Logout" : "Login"
        ),
        h("span", {
          className: "status-dot " + (connected ? "green" : "red"),
        }),
        connected ? "Live" : "Offline",
        h(
          "span",
          { className: "hide-mobile" },
          lastUpdate ? " \u00B7 " + lastUpdate : "",
        ),
        h(
          "span",
          {
            className: "section-badge",
            title: "Queued and running workflows",
          },
          "Queue " +
            ((queue.queued_count || 0) +
              "/" +
              (queue.in_progress_count || 0)),
        ),
        h(
          "span",
          {
            className: "section-badge",
            title:
              watchdog && watchdog.summary
                ? watchdog.summary
                : "WSL keepalive and watchdog state",
          },
          "Keepalive " +
            (watchdog && watchdog.status ? watchdog.status : "unknown"),
        ),
        h(
          "span",
          {
            title: githubStatus.detail || "GitHub API status",
            className:
              "section-badge" +
              (githubStatus.status === "rate_limited" || githubStatus.status === "auth_error"
                ? " section-badge--orange"
                : ""),
          },
          "GitHub " + (githubStatus.status || "unknown"),
        ),
        h(
          "span",
          {
            className: "section-badge",
            title: deployment.deployed_at || "Deployment revision",
          },
          "Build " + shortSha(deployment.git_sha),
        ),
        h(AlertsCenter, {
          alerts: appAlerts,
          onNavigate: onAlertNavigate,
        }),
        h("button", {
          className: "btn legacy-toolstrip-button" + (asstOpen ? " legacy-toolstrip-button--active" : ""),
          onClick: toggleAsst,
          title: "Toggle Chat sidebar",
        }, "💬 Chat"),
        h(QuickDispatchPopover, null),
        h(
          "button",
          {
            className: "btn legacy-toolstrip-button",
            onClick: function () {
              fetchFleet();
              fetchRepos();
              fetchTests();
              fetchReports();
              fetchQueue();
              fetchMachines();
              fetchLocalApps();
              fetchEnrichedRuns();
              fetchWatchdog();
              fetchDeployment();
              fetchDeploymentState();
              fetchRemediationConfig();
              fetchScheduledJobs();
              fetchRunnerCapacity();
            },
          },
          I.refresh(12),
        ),
      ),
      ), // close app-header__row--secondary
    ),
    h(
      "div",
      {
        className:
          "legacy-dashboard-layout" +
          (asstPosition === "left" ? " legacy-dashboard-layout--assistant-left" : ""),
      },
      h(
        "div",
        // In chromeless mode the modern desktop shell (#802) already provides
        // the single `main` landmark, so this inner region drops role="main" to
        // avoid duplicate landmarks.
        { className: "main-content legacy-dashboard-layout__main", role: chromeless ? undefined : "main" },
        tab === "overview"
        ? h("div", null,
          h(FleetTab, {
            runners: runners,
            runs: runs,
            system: system,
            stats: stats,
            queue: queue,
            machinesData: machinesData,
            onFleet: onFleet,
            onRunner: onRunner,
            loading: actionLoading,
            watchdog: watchdog,
            deployment: deployment,
            setTab: setTab,             // for hero KPI buttons that navigate to other tabs
            runnerAudit: runnerAudit,   // for hosted-runner billing alert in fleet hero
            onOpenDeployment: function () {
              setTab("deployment");
              fetchDeploymentState();
            },
          }),
          // Issue #863: at-a-glance alarm panel + recent event log on Overview.
          h("div", { className: "section section--stacked" },
            h("div", { className: "section-header" },
              h("div", { className: "section-title" },
                I.activity(16),
                "Alarms & Recent Events"
              ),
              h("button", {
                className: "btn section-header__action",
                onClick: function () { setTab("events"); },
              }, "Open Event Log")
            ),
            h("div", { className: "section-body" },
              h(OverviewEventSection, { rollupAlerts: appAlerts, onNavigate: onAlertNavigate })
            )
          ),
          h(OverviewLeases)
        )
        : tab === "deployment"
          ? h(DeploymentTab, {
              data: deploymentState,
              loading: deploymentStateLoading,
              onRefresh: fetchDeploymentState,
              onOpenFleet: function () {
                setTab("overview");
                fetchFleet();
              },
            })
          : tab === "agent-dispatch"
            ? h(AgentDispatchPage)
            : tab === "conductor"
            ? h(Conductor)
            : tab === "remediation"
            ? h(RemediationTab, {
                config: remediationConfig,
                workflows: remediationWorkflows,
                runs: enrichedRuns.length ? enrichedRuns : runs,
                loading: remediationLoading,
                error: remediationError,
                selectedRunId: remediationSelectedRunId,
                setSelectedRunId: setRemediationSelectedRunId,
                provider: remediationProvider,
                setProvider: setRemediationProvider,
                model: remediationModel,
                setModel: setRemediationModel,
                plan: remediationPlan,
                dispatchState: remediationDispatchState,
                onRefresh: fetchRemediationConfig,
                onSaveConfig: saveRemediationConfig,
                onPreview: previewRemediation,
                onDispatch: dispatchRemediation,
                history: remediationHistory,
                principalName: principal && principal.name,
              })
            : isAnalysisTabKey(tab)
              ? h(AnalysisTab, {
                  activeTab: tab,
                  runs: enrichedRuns,
                  runners: runners,
                  reports: reports,
                  reportsLoading: reportsLoading,
                })
              : tab === "queue"
                ? h(QueueTab, {
                    queue: queue,
                    loading: queueLoading,
                    onRefresh: fetchQueue,
                  })
                : tab === "machines"
                  ? h(MachinesTab, {
                      data: machinesData,
                      loading: machinesLoading,
                      runners: runners,
                    })
                  : tab === "org"
                    ? h(OrgTab, {
                        repos: repos,
                        loading: reposLoading,
                        stats: stats,
                      })
                    : tab === "tests"
                      ? h(TestsTab, {
                          testRepos: testRepos,
                          loading: testsLoading,
                          ciResults: ciResults,
                        })
                      : tab === "scheduled-jobs"
                            ? h(ScheduledJobs, null)
                            : tab === "workflows"
                              ? h(WorkflowsTab, {
                                  workflows: workflowsList,
                                  loading: workflowsListLoading,
                                  error: workflowsListError,
                                  onDispatch: dispatchWorkflow,
                                  onRefresh: fetchWorkflowsList,
                                })
                              : tab === "runner-schedule"
                                ? h(RunnerScheduleTab, {
                                    data: runnerCapacity,
                                    loading: runnerCapacityLoading,
                                    onRefresh: fetchRunnerCapacity,
                                    onSave: saveRunnerCapacity,
                                  })
                                : tab === "local-apps"
                                  ? h(LocalAppsTab, {
                                        key: "local-apps-boundary",
                                        data: localApps,
                                        loading: localAppsLoading,
                                        onRefresh: fetchLocalApps,
                                      })
                                  : tab === "credentials"
                                    ? h(CredentialsTab, {
                                        probes:
                                          credentialsData.probes || [],
                                        summary:
                                          credentialsData.summary || {},
                                        loading: credentialsLoading,
                                        error: credentialsError,
                                        onRefresh: fetchCredentials,
                                        onSetKey: setCredentialKey,
                                        mobile: mobileCredentialsViewport,
                                      })
                                  : tab === "fleet-orchestration"
                                    ? h(FleetOrchestrationTab, {
                                        data: fleetOrchData,
                                        loading: fleetOrchLoading,
                                        error: fleetOrchError,
                                        onRefresh:
                                          fetchFleetOrchestration,
                                        onDispatch: orchDispatch,
                                        onDeploy: orchDeploy,
                                      })
                                    : tab === "assessments"
                                      ? h(AssessmentsTab, {
                                          repos: repos,
                                          scores: assessmentScores,
                                          loading: assessmentLoading,
                                          error: assessmentError,
                                          onDispatch: dispatchAssessment,
                                          onRefresh: fetchAssessments,
                                        })
                                      : tab === "feature-requests"
                                        ? h(FeatureRequestsTab, {
                                            repos: repos,
                                            requests: featureRequests,
                                            templates: promptTemplates,
                                            standards: featureStandards,
                                            loading: featureRequestsLoading,
                                            promptNotes: promptNotes,
                                            onDispatch: dispatchFeatureRequest,
                                            onSaveTemplate: savePromptTemplate,
                                            onSavePromptNotes: updatePromptNotes,
                                            onRefresh: fetchFeatureRequests,
                                          })
                                        : tab === "maxwell"
                                          ? h(MaxwellTab, {
                                              status: maxwellStatus,
                                              loading: maxwellLoading,
                                              error: maxwellError,
                                              onRefresh:
                                                fetchMaxwellStatus,
                                              onControl: maxwellControl,
                                            })
                                          : tab === "cline-launcher"
                                            ? h(ClineLauncherTab, null)
                                            : tab === "diagnostics"
                                              ? h(DiagnosticsTab, null)
                                              : tab === "runner-audit"
                                                ? h(RunnerAudit, {
                                                    audit: runnerAudit,
                                                    onRefresh: triggerRunnerAuditRefresh,
                                                  })
                                                : tab === "principals"
                                                  ? h(PrincipalsTab, null)
                                                  : tab === "settings"
                                                    ? h(ThemeSettings, null)
                                                    : tab === "linear-setup"
                                                      ? h(LinearSetup, null)
                                                      : tab === "push-settings"
                                                        ? h(PushSettings, null)
                                                        : tab === "events"
                                                          ? h(EventsTab, { rollupAlerts: appAlerts, onNavigate: onAlertNavigate })
                                                          : null,
      ),
      h(AssistantSidebar, { currentTab: tab, open: asstOpen, onToggle: toggleAsst }),
    ),
    recoveryModal && recoveryModal.visible
      ? h(RecoveryDialog, { onClose: function () { setRecoveryModal(null); } })
      : null,
    h(SessionExpiredDialog, {
      open: sessionExpiredOpen,
      onClose: function () { setSessionExpiredOpen(false); },
    }),
    h(DashboardHelp, { currentTab: tab }),
  );
}

export default App;
