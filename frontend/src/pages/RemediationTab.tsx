/**
 * RemediationTab.tsx — the full desktop "Remediation" tab, extracted
 * (behaviour 1:1) from the legacy `App.tsx` monolith as part of the
 * decomposition epic (#836, pass 11).
 *
 * Owns the three-way sub-tab strip (Automations / PRs / Issues), the mobile
 * remediation action sheet, and the desktop "Automations" view: manual
 * dispatch list, inline-editable loop-guard / default-provider stats,
 * workflow-type routing config, provider availability, remediation history,
 * plan preview, and the Jules workflow-health panel.
 *
 * Presentational: all remediation data + handlers are owned by the legacy App
 * and threaded in as props (config, workflows, runs, plan, dispatchState, and
 * the on* callbacks), exactly mirroring the legacy call site. The PRs/Issues
 * sub-tabs are self-contained and only need `principalName`. Glyphs come from
 * `decompIcons`, the sub-tab strip + stat card from the canonical
 * `components/*`, and the Jules manual-dispatch helper from
 * `lib/remediationJules`.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- 1:1 port of dynamically-typed legacy remediation payloads; the backend response shapes lack complete TypeScript definitions. */
import React from "react";
import { PROVIDER_MODELS } from "../lib/providerModels";
import { dispatchJulesWorkflow, type JulesDispatchMsg } from "../lib/remediationJules";
import { SubTabs } from "../components/SubTabs";
import { Stat } from "../components/Stat";
import {
  ActivityGlyph,
  ClockGlyph,
  IssueGlyph,
  RefreshGlyph,
  ServerGlyph,
  SettingsGlyph,
} from "./decompIcons";
import { RemediationPRsSubTab } from "./RemediationPRs";
import { RemediationIssuesSubTab } from "./RemediationIssues";

const h = React.createElement;

export interface RemediationTabProps {
  config?: any;
  workflows?: any;
  runs?: any[];
  loading?: boolean;
  error?: string | null;
  selectedRunId?: string | null;
  setSelectedRunId: (id: string) => void;
  provider?: string;
  setProvider: (id: string) => void;
  model?: string;
  setModel?: (id: string) => void;
  plan?: any;
  dispatchState?: any;
  onRefresh: () => void;
  onSaveConfig: (policy: any) => unknown;
  onPreview: (run: any) => void;
  onDispatch: (run: any) => void;
  history?: any[];
  principalName?: string;
}

export function RemediationTab(p: RemediationTabProps): React.ReactElement {
  const config = p.config || {};
  const workflows = p.workflows || {};
  const runs = p.runs || [];
  const loading = p.loading;
  const error = p.error;
  const selectedRunId = p.selectedRunId;
  const setSelectedRunId = p.setSelectedRunId;
  const provider = p.provider;
  const setProvider = p.setProvider;
  const model = p.model || "";
  const setModel = p.setModel || function () {};
  const plan = p.plan;
  const dispatchState = p.dispatchState;
  const onRefresh = p.onRefresh;
  const onSaveConfig = p.onSaveConfig;
  const onPreview = p.onPreview;
  const onDispatch = p.onDispatch;
  const history = p.history || [];
  const failedRuns = runs.filter(function (run: any) {
    return run.conclusion === "failure";
  });
  const selectedRun =
    failedRuns.find(function (run: any) {
      return String(run.id) === String(selectedRunId);
    }) ||
    failedRuns[0] ||
    null;
  const policy = config.policy || {};
  const providers = config.providers || {};
  const availability = config.availability || {};
  const providerOrder = [
    "jules_api",
    "codex_cli",
    "claude_code_cli",
    "gemini_cli",
    "ollama",
    "cline",
  ];
  const providerEntries = Object.keys(providers).length
    ? Object.keys(providers).map(function (providerId) {
        return [providerId, providers[providerId]];
      })
    : providerOrder.map(function (providerId) {
        return [providerId, { label: providerId, notes: "" }];
      });
  const drr = React.useState(policy.workflow_type_rules || {});
  const draftRules = drr[0],
    setDraftRules = drr[1];
  const sps = React.useState(false);
  const savingPolicy = sps[0],
    setSavingPolicy = sps[1];
  const lge = React.useState(false);
  const editingLoopGuard = lge[0],
    setEditingLoopGuard = lge[1];
  const lgv = React.useState(
    policy.max_same_failure_attempts != null
      ? String(policy.max_same_failure_attempts)
      : "3",
  );
  const loopGuardValue = lgv[0],
    setLoopGuardValue = lgv[1];
  const dpe = React.useState(false);
  const editingDefaultProvider = dpe[0],
    setEditingDefaultProvider = dpe[1];
  // Inline status for Jules dispatch – replaces alert() (issue #51)
  const jdm = React.useState<JulesDispatchMsg | null>(null);
  const julesDispatchMsg = jdm[0],
    setJulesDispatchMsg = jdm[1];
  const mrs = React.useState<any>(null);
  const mobileRemediationSheetRun = mrs[0],
    setMobileRemediationSheetRun = mrs[1];
  const mrp = React.useState(false);
  const mobileRemediationPickerOpen = mrp[0],
    setMobileRemediationPickerOpen = mrp[1];
  React.useEffect(
    function () {
      setDraftRules((config.policy && config.policy.workflow_type_rules) || {});
      setLoopGuardValue(
        config.policy && config.policy.max_same_failure_attempts != null
          ? String(config.policy.max_same_failure_attempts)
          : "3",
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps -- legacy 1:1: re-sync drafts only when the upstream config object changes; the useState setters are stable.
    [config],
  );
  function updateRule(workflowType: string, fieldName: string, value: any) {
    setDraftRules(function (prev: any) {
      const next = Object.assign({}, prev);
      next[workflowType] = Object.assign({}, prev[workflowType] || {}, {
        [fieldName]: value,
      });
      return next;
    });
  }
  function savePolicy(extraFields?: any) {
    setSavingPolicy(true);
    Promise.resolve(
      onSaveConfig(
        Object.assign({}, policy, extraFields || {}, {
          workflow_type_rules: draftRules,
        }),
      ),
    ).finally(function () {
      setSavingPolicy(false);
    });
  }
  function saveLoopGuard() {
    const v = parseInt(loopGuardValue, 10);
    if (!isNaN(v) && v > 0) {
      savePolicy({ max_same_failure_attempts: v });
    }
    setEditingLoopGuard(false);
  }
  function saveDefaultProvider(val: string) {
    savePolicy({ default_provider: val });
    setEditingDefaultProvider(false);
  }
  function providerLabel(providerId: string) {
    const entry = providerEntries.find(function (providerEntry: any) {
      return providerEntry[0] === providerId;
    });
    return (entry && entry[1] && (entry[1] as any).label) || providerId;
  }
  function recommendedProviderId() {
    return (
      (plan && plan.decision && plan.decision.provider_id) ||
      provider ||
      policy.default_provider ||
      "jules_api"
    );
  }
  function remediationRunTitle(run: any) {
    if (!run) return "Failed run";
    const repoName =
      run.repository && run.repository.name ? run.repository.name : "repo";
    return (
      repoName +
      " / " +
      (run.name || run.workflow_name || "workflow") +
      " #" +
      run.id
    );
  }
  function openMobileRemediationSheet(run: any) {
    setSelectedRunId(String(run.id));
    setMobileRemediationPickerOpen(false);
    setMobileRemediationSheetRun(run);
  }
  function dispatchFromMobileSheet(run: any) {
    setSelectedRunId(String(run.id));
    onDispatch(run);
    setMobileRemediationSheetRun(null);
  }
  const accepted = !!(plan && plan.decision && plan.decision.accepted);
  const sta = React.useState(
    (function () {
      try {
        return localStorage.getItem("remediation-subtab") || "automations";
      } catch (e) {
        return "automations";
      }
    })(),
  );
  const subTab = sta[0],
    setSubTab = sta[1];

  return h(
    "div",
    null,
    h(SubTabs, {
      tabs: [
        { key: "automations", label: "Automations" },
        { key: "prs", label: "PRs" },
        { key: "issues", label: "Issues" },
      ],
      activeKey: subTab,
      onChange: setSubTab,
      storageKey: "remediation-subtab",
      className: "remediation-mobile-tabs",
    }),
    dispatchState
      ? h(
          "div",
          {
            className: "remediation-inflight-tile",
            role: "status",
            "aria-live": "polite",
          },
          h(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
                flexWrap: "wrap",
              },
            },
            h(
              "strong",
              { style: { fontSize: 13 } },
              dispatchState.error ? "Dispatch needs attention" : "Agent working",
            ),
            h(
              "span",
              { className: "section-badge" },
              dispatchState.error ? "error" : "in flight",
            ),
          ),
          h(
            "div",
            {
              style: {
                marginTop: 6,
                fontSize: 12,
                color: dispatchState.error
                  ? "var(--accent-red)"
                  : "var(--text-secondary)",
              },
            },
            dispatchState.error ||
              dispatchState.note ||
              "Dispatch submitted. Waiting for the next history refresh.",
          ),
        )
      : null,
    mobileRemediationSheetRun
      ? (function () {
          const sheetRun = mobileRemediationSheetRun;
          const repoName =
            sheetRun.repository && sheetRun.repository.name
              ? sheetRun.repository.name
              : "repo";
          const branch = sheetRun.head_branch || "branch";
          const ghUrl =
            sheetRun.html_url ||
            "https://github.com/D-sorganization/" +
              repoName +
              "/actions/runs/" +
              sheetRun.id;
          const recommendedId = recommendedProviderId();
          return h(
            "div",
            {
              className: "mobile-remediation-sheet",
              role: "dialog",
              "aria-modal": "true",
              "aria-label": "Mobile remediation dispatch",
              onClick: function (e: any) {
                if (e.target === e.currentTarget) {
                  setMobileRemediationSheetRun(null);
                }
              },
            },
            h(
              "div",
              { className: "mobile-remediation-sheet-panel" },
              h(
                "div",
                {
                  style: {
                    display: "flex",
                    alignItems: "flex-start",
                    justifyContent: "space-between",
                    gap: 12,
                    marginBottom: 12,
                  },
                },
                h(
                  "div",
                  { style: { minWidth: 0 } },
                  h(
                    "div",
                    {
                      style: {
                        fontSize: 14,
                        fontWeight: 700,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      },
                    },
                    remediationRunTitle(sheetRun),
                  ),
                  h(
                    "div",
                    {
                      style: {
                        marginTop: 3,
                        fontSize: 12,
                        color: "var(--text-muted)",
                      },
                    },
                    "Branch " +
                      branch +
                      " | recommended " +
                      providerLabel(recommendedId),
                  ),
                ),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      setMobileRemediationSheetRun(null);
                    },
                  },
                  "Close",
                ),
              ),
              h(
                "div",
                {
                  style: {
                    display: "grid",
                    gap: 8,
                  },
                },
                h(
                  "button",
                  {
                    className: "btn btn-primary",
                    disabled: loading,
                    onClick: function () {
                      dispatchFromMobileSheet(sheetRun);
                    },
                    style: {
                      justifyContent: "center",
                      padding: "10px 12px",
                      fontSize: 13,
                    },
                  },
                  "Dispatch " + providerLabel(recommendedId),
                ),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      setMobileRemediationPickerOpen(!mobileRemediationPickerOpen);
                    },
                    style: { justifyContent: "center" },
                  },
                  mobileRemediationPickerOpen
                    ? "Hide agent picker"
                    : "Pick agent...",
                ),
                mobileRemediationPickerOpen
                  ? h(
                      "select",
                      {
                        value: provider,
                        onChange: function (e: any) {
                          setProvider(e.target.value);
                        },
                        style: {
                          width: "100%",
                          background: "var(--bg-secondary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border)",
                          borderRadius: 6,
                          padding: "9px 10px",
                        },
                      },
                      providerEntries.map(function (entry: any) {
                        return h(
                          "option",
                          { key: "mobile-agent-" + entry[0], value: entry[0] },
                          entry[1].label || entry[0],
                        );
                      }),
                    )
                  : null,
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      onPreview(sheetRun);
                      setMobileRemediationSheetRun(null);
                    },
                    style: { justifyContent: "center" },
                  },
                  "Preview safety plan",
                ),
                h(
                  "a",
                  {
                    className: "btn",
                    href: ghUrl,
                    target: "_blank",
                    rel: "noopener noreferrer",
                    style: {
                      justifyContent: "center",
                      textDecoration: "none",
                    },
                  },
                  "Open on desktop",
                ),
              ),
            ),
          );
        })()
      : null,
    subTab === "automations" &&
      h(
        "div",
        null,
        // ── Manual Dispatch section (TOP) ────────────────────────────────
        h(
          "div",
          { className: "section", style: { marginBottom: 16 } },
          h(
            "div",
            { className: "section-header" },
            h(
              "span",
              { className: "section-title" },
              h(IssueGlyph, { size: 14 }),
              "Manual Dispatch",
            ),
            h(
              "button",
              { className: "btn", onClick: onRefresh },
              h(RefreshGlyph, { size: 12 }),
              "Refresh",
            ),
          ),
          h(
            "div",
            { className: "section-body" },
            error
              ? h(
                  "div",
                  {
                    style: {
                      marginBottom: 12,
                      padding: "10px 12px",
                      borderRadius: 8,
                      background: "rgba(248,81,73,0.12)",
                      color: "var(--accent-red)",
                      fontSize: 12,
                    },
                  },
                  error,
                )
              : null,
            failedRuns.length === 0
              ? h(
                  "div",
                  {
                    style: {
                      color: "var(--text-muted)",
                      fontSize: 12,
                      padding: "8px 0",
                    },
                  },
                  "No failed runs in the current dashboard sample.",
                )
              : failedRuns.map(function (run: any) {
                  const isSelected =
                    String(run.id) === String(selectedRunId) ||
                    (!selectedRunId &&
                      selectedRun &&
                      String(run.id) === String(selectedRun.id));
                  const repoName =
                    run.repository && run.repository.name
                      ? run.repository.name
                      : "repo";
                  const workflowName = run.name || run.workflow_name || "workflow";
                  const branch = run.head_branch || "branch";
                  const ghUrl =
                    run.html_url ||
                    "https://github.com/D-sorganization/" +
                      repoName +
                      "/actions/runs/" +
                      run.id;
                  return h(
                    "div",
                    {
                      key: run.id,
                      onClick: function () {
                        openMobileRemediationSheet(run);
                      },
                      style: {
                        display: "flex",
                        alignItems: "center",
                        gap: 10,
                        padding: "10px 12px",
                        marginBottom: 6,
                        borderRadius: 8,
                        border:
                          "1px solid " +
                          (isSelected ? "var(--accent-green)" : "var(--border)"),
                        background: isSelected
                          ? "rgba(63,185,80,0.07)"
                          : "var(--bg-secondary)",
                        cursor: "pointer",
                      },
                    },
                    h(
                      "div",
                      { style: { flex: 1, minWidth: 0 } },
                      h(
                        "div",
                        {
                          style: {
                            fontSize: 13,
                            fontWeight: 600,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          },
                        },
                        repoName +
                          " \xB7 " +
                          workflowName +
                          " \xB7 " +
                          branch +
                          " #" +
                          run.id,
                      ),
                      h(
                        "div",
                        {
                          style: {
                            fontSize: 11,
                            color: "var(--text-muted)",
                            marginTop: 2,
                          },
                        },
                        run.created_at
                          ? run.created_at.replace("T", " ").slice(0, 19) +
                              " UTC"
                          : "",
                      ),
                    ),
                    h(
                      "div",
                      {
                        style: {
                          display: "flex",
                          gap: 4,
                          flexWrap: "wrap",
                          justifyContent: "flex-end",
                        },
                      },
                      [
                        {
                          label: "Run",
                          href: ghUrl,
                        },
                        {
                          label: "Repo",
                          href:
                            run.repository && run.repository.html_url
                              ? run.repository.html_url
                              : "https://github.com/D-sorganization/" + repoName,
                        },
                        {
                          label: "Branch",
                          href:
                            "https://github.com/D-sorganization/" +
                            repoName +
                            "/tree/" +
                            encodeURIComponent(branch),
                        },
                        {
                          label: "Logs",
                          href: ghUrl + "/logs",
                        },
                      ].map(function (link) {
                        return h(
                          "a",
                          {
                            key: link.label,
                            href: link.href,
                            target: "_blank",
                            rel: "noopener noreferrer",
                            onClick: function (e: any) {
                              e.stopPropagation();
                            },
                            style: {
                              fontSize: 10,
                              color: "var(--accent-green)",
                              textDecoration: "none",
                              padding: "3px 6px",
                              border: "1px solid var(--accent-green)",
                              borderRadius: 4,
                              whiteSpace: "nowrap",
                            },
                          },
                          "↗ " + link.label,
                        );
                      }),
                    ),
                    h(
                      "select",
                      {
                        value: isSelected
                          ? provider
                          : policy.default_provider || "jules_api",
                        onClick: function (e: any) {
                          e.stopPropagation();
                          setSelectedRunId(String(run.id));
                        },
                        onChange: function (e: any) {
                          e.stopPropagation();
                          setSelectedRunId(String(run.id));
                          setProvider(e.target.value);
                        },
                        style: {
                          background: "var(--bg-primary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border)",
                          borderRadius: 6,
                          padding: "4px 8px",
                          fontSize: 12,
                        },
                      },
                      providerEntries.map(function (entry: any) {
                        return h(
                          "option",
                          { key: entry[0], value: entry[0] },
                          entry[1].label || entry[0],
                        );
                      }),
                    ),
                    (function () {
                      const currentProvider = isSelected
                        ? provider
                        : policy.default_provider || "jules_api";
                      const modelOpts = (PROVIDER_MODELS as any)[currentProvider];
                      if (!modelOpts || !isSelected) return null;
                      return h(
                        "select",
                        {
                          value:
                            model || (modelOpts[0] && modelOpts[0].value) || "",
                          onClick: function (e: any) {
                            e.stopPropagation();
                          },
                          onChange: function (e: any) {
                            e.stopPropagation();
                            setModel(e.target.value);
                          },
                          style: {
                            background: "var(--bg-primary)",
                            color: "var(--text-muted)",
                            border: "1px solid var(--border)",
                            borderRadius: 6,
                            padding: "4px 8px",
                            fontSize: 11,
                          },
                        },
                        modelOpts.map(function (m: any) {
                          return h(
                            "option",
                            { key: m.value, value: m.value },
                            m.label,
                          );
                        }),
                      );
                    })(),
                    h(
                      "button",
                      {
                        className: "btn",
                        onClick: function (e: any) {
                          e.stopPropagation();
                          setSelectedRunId(String(run.id));
                          onPreview(run);
                        },
                        disabled: loading,
                        style: { whiteSpace: "nowrap" },
                      },
                      "Preview",
                    ),
                    h(
                      "button",
                      {
                        className: "btn",
                        onClick: function (e: any) {
                          e.stopPropagation();
                          setSelectedRunId(String(run.id));
                          onDispatch(run);
                        },
                        disabled: loading || (isSelected && !accepted),
                        style: {
                          whiteSpace: "nowrap",
                          background:
                            accepted && isSelected
                              ? "rgba(63,185,80,0.2)"
                              : undefined,
                        },
                      },
                      "Dispatch",
                    ),
                  );
                }),
          ),
        ),
        // ── Stat row with inline-editable fields ──────────────────────────
        h(
          "div",
          { className: "stat-row" },
          h(
            "div",
            {
              className: "stat-card",
              style: { cursor: "pointer" },
              onClick: function () {
                setEditingLoopGuard(true);
              },
            },
            h("div", { className: "stat-label" }, "Loop guard"),
            editingLoopGuard
              ? h(
                  "div",
                  {
                    style: { display: "flex", gap: 6, alignItems: "center" },
                  },
                  h("input", {
                    type: "number",
                    min: 1,
                    max: 20,
                    value: loopGuardValue,
                    autoFocus: true,
                    onChange: function (e: any) {
                      setLoopGuardValue(e.target.value);
                    },
                    onKeyDown: function (e: any) {
                      if (e.key === "Enter") saveLoopGuard();
                      if (e.key === "Escape") setEditingLoopGuard(false);
                    },
                    style: {
                      width: 60,
                      background: "var(--bg-primary)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--accent-green)",
                      borderRadius: 4,
                      padding: "2px 6px",
                      fontSize: 18,
                      fontWeight: 700,
                    },
                  }),
                  h(
                    "button",
                    {
                      className: "btn",
                      onClick: function (e: any) {
                        e.stopPropagation();
                        saveLoopGuard();
                      },
                      style: { padding: "2px 8px", fontSize: 11 },
                    },
                    "Save",
                  ),
                )
              : h(
                  "div",
                  { style: { fontSize: 24, fontWeight: 700 } },
                  policy.max_same_failure_attempts != null
                    ? policy.max_same_failure_attempts
                    : 3,
                ),
            h(
              "div",
              { className: "stat-sub" },
              editingLoopGuard ? "press Enter to save" : "click to edit",
            ),
          ),
          h(
            "div",
            {
              className: "stat-card",
              style: { cursor: "pointer" },
              onClick: function () {
                setEditingDefaultProvider(true);
              },
            },
            h("div", { className: "stat-label" }, "Default provider"),
            editingDefaultProvider
              ? h(
                  "select",
                  {
                    autoFocus: true,
                    value: policy.default_provider || "jules_api",
                    onChange: function (e: any) {
                      saveDefaultProvider(e.target.value);
                    },
                    onBlur: function () {
                      setEditingDefaultProvider(false);
                    },
                    style: {
                      background: "var(--bg-primary)",
                      color: "var(--text-primary)",
                      border: "1px solid var(--accent-green)",
                      borderRadius: 4,
                      padding: "2px 6px",
                      fontSize: 13,
                      fontWeight: 700,
                    },
                  },
                  providerEntries.map(function (entry: any) {
                    return h(
                      "option",
                      { key: entry[0], value: entry[0] },
                      entry[1].label || entry[0],
                    );
                  }),
                )
              : h(
                  "div",
                  { style: { fontSize: 16, fontWeight: 700 } },
                  policy.default_provider || "jules_api",
                ),
            h(
              "div",
              { className: "stat-sub" },
              editingDefaultProvider ? "select to save" : "click to edit",
            ),
          ),
          h(Stat, {
            label: "Failed runs",
            value: failedRuns.length,
            sub: "current dashboard sample",
          }),
          h(Stat, {
            label: "Dispatch history",
            value: history.length,
            sub: "recent dispatches",
          }),
          h(Stat, {
            label: "Jules workflows",
            value: (workflows.workflows || []).length,
            sub: "health visibility",
          }),
        ),
        // ── Two-column grid ───────────────────────────────────────────────
        h(
          "div",
          {
            style: {
              display: "grid",
              gridTemplateColumns: "minmax(320px, 420px) 1fr",
              gap: 16,
              marginTop: 16,
            },
          },
          // Left column: Auto config + Providers
          h(
            "div",
            null,
            h(
              "div",
              { className: "section" },
              h(
                "div",
                { className: "section-header" },
                h(
                  "span",
                  { className: "section-title" },
                  h(SettingsGlyph, { size: 14 }),
                  "Automatic remediation configuration",
                ),
                h(
                  "button",
                  {
                    className: "btn",
                    onClick: function () {
                      savePolicy();
                    },
                    disabled: savingPolicy || loading,
                  },
                  savingPolicy ? "Saving…" : "Save routing",
                ),
              ),
              h(
                "div",
                { className: "section-body" },
                h(
                  "div",
                  {
                    style: {
                      fontSize: 12,
                      color: "var(--text-secondary)",
                      marginBottom: 12,
                    },
                  },
                  "Workflow Type Routing lets simple failures auto-dispatch while complex failures can stay manual until reviewed.",
                ),
                h(
                  "div",
                  {
                    style: {
                      fontSize: 13,
                      fontWeight: 600,
                      marginBottom: 10,
                    },
                  },
                  "Workflow Type Routing",
                ),
                Object.keys(draftRules).map(function (workflowType: string) {
                  const rule = draftRules[workflowType] || {};
                  return h(
                    "div",
                    {
                      key: workflowType,
                      style: {
                        background: "var(--bg-secondary)",
                        border: "1px solid var(--border)",
                        borderRadius: 8,
                        padding: 12,
                        marginBottom: 10,
                      },
                    },
                    h(
                      "div",
                      {
                        style: {
                          display: "grid",
                          gridTemplateColumns: "1.2fr 1fr 1fr",
                          gap: 10,
                          marginBottom: 8,
                        },
                      },
                      h(
                        "div",
                        null,
                        h(
                          "div",
                          { style: { fontSize: 13, fontWeight: 600 } },
                          rule.label || workflowType,
                        ),
                        h(
                          "div",
                          {
                            style: {
                              marginTop: 4,
                              fontSize: 11,
                              color: "var(--text-muted)",
                            },
                          },
                          (rule.match_terms || []).join(", ") || "fallback",
                        ),
                      ),
                      h(
                        "label",
                        {
                          style: {
                            fontSize: 12,
                            color: "var(--text-secondary)",
                          },
                        },
                        "Dispatch mode",
                        h(
                          "select",
                          {
                            value: rule.dispatch_mode || "manual",
                            onChange: function (e: any) {
                              updateRule(
                                workflowType,
                                "dispatch_mode",
                                e.target.value,
                              );
                            },
                            style: {
                              width: "100%",
                              marginTop: 6,
                              background: "var(--bg-primary)",
                              color: "var(--text-primary)",
                              border: "1px solid var(--border)",
                              borderRadius: 6,
                              padding: "8px 10px",
                            },
                          },
                          h("option", { value: "auto" }, "Auto"),
                          h("option", { value: "manual" }, "Manual"),
                        ),
                      ),
                      h(
                        "label",
                        {
                          style: {
                            fontSize: 12,
                            color: "var(--text-secondary)",
                          },
                        },
                        "Provider",
                        h(
                          "select",
                          {
                            value:
                              rule.provider_id ||
                              policy.default_provider ||
                              "jules_api",
                            onChange: function (e: any) {
                              updateRule(
                                workflowType,
                                "provider_id",
                                e.target.value,
                              );
                            },
                            style: {
                              width: "100%",
                              marginTop: 6,
                              background: "var(--bg-primary)",
                              color: "var(--text-primary)",
                              border: "1px solid var(--border)",
                              borderRadius: 6,
                              padding: "8px 10px",
                            },
                          },
                          providerEntries.map(function (entry: any) {
                            return h(
                              "option",
                              {
                                key: workflowType + "-" + entry[0],
                                value: entry[0],
                              },
                              entry[1].label || entry[0],
                            );
                          }),
                        ),
                      ),
                    ),
                    h(
                      "label",
                      {
                        style: {
                          fontSize: 12,
                          color: "var(--text-secondary)",
                        },
                      },
                      "Fallback providers (loop guard escalation)",
                      h(
                        "select",
                        {
                          multiple: true,
                          value: rule.fallback_providers || [],
                          onChange: function (e: any) {
                            const selected = [];
                            for (let i = 0; i < e.target.options.length; i++) {
                              if (e.target.options[i].selected) {
                                selected.push(e.target.options[i].value);
                              }
                            }
                            updateRule(
                              workflowType,
                              "fallback_providers",
                              selected,
                            );
                          },
                          style: {
                            width: "100%",
                            marginTop: 6,
                            background: "var(--bg-primary)",
                            color: "var(--text-primary)",
                            border: "1px solid var(--border)",
                            borderRadius: 6,
                            padding: "4px 6px",
                            height: 72,
                          },
                        },
                        providerEntries.map(function (entry: any) {
                          return h(
                            "option",
                            {
                              key: workflowType + "-fb-" + entry[0],
                              value: entry[0],
                            },
                            entry[1].label || entry[0],
                          );
                        }),
                      ),
                    ),
                  );
                }),
              ),
            ),
            h(
              "div",
              { className: "section", style: { marginTop: 16 } },
              h(
                "div",
                { className: "section-header" },
                h(
                  "span",
                  { className: "section-title" },
                  h(ServerGlyph, { size: 14 }),
                  "Providers",
                ),
              ),
              h(
                "div",
                { className: "section-body" },
                providerEntries.map(function (entry: any) {
                  const providerId = entry[0];
                  const providerMeta = entry[1];
                  const state = availability[providerId] || {};
                  return h(
                    "div",
                    {
                      key: providerId,
                      style: {
                        padding: "10px 0",
                        borderBottom: "1px solid var(--border)",
                      },
                    },
                    h(
                      "div",
                      {
                        style: {
                          display: "flex",
                          justifyContent: "space-between",
                          gap: 12,
                        },
                      },
                      h(
                        "span",
                        { style: { fontSize: 13, fontWeight: 600 } },
                        providerMeta.label,
                      ),
                      h(
                        "span",
                        {
                          className: "section-badge",
                          style: {
                            background: state.available
                              ? "rgba(63,185,80,0.15)"
                              : "rgba(210,153,34,0.15)",
                            color: state.available
                              ? "var(--accent-green)"
                              : "var(--accent-yellow)",
                          },
                        },
                        state.status || "unknown",
                      ),
                    ),
                    h(
                      "div",
                      {
                        style: {
                          marginTop: 4,
                          fontSize: 12,
                          color: "var(--text-muted)",
                        },
                      },
                      providerMeta.notes || "",
                    ),
                  );
                }),
              ),
            ),
          ),
          // Right column: History + Plan Preview + Jules Workflow Health
          h(
            "div",
            null,
            h(
              "div",
              { className: "section" },
              h(
                "div",
                { className: "section-header" },
                h(
                  "span",
                  { className: "section-title" },
                  h(ClockGlyph, { size: 14 }),
                  "Remediation History",
                ),
              ),
              h(
                "div",
                { className: "section-body" },
                history.length === 0
                  ? h(
                      "div",
                      { style: { color: "var(--text-muted)", fontSize: 12 } },
                      "No dispatch history yet. History is recorded after each manual dispatch.",
                    )
                  : history.map(function (entry: any, idx: number) {
                      const ts = entry.timestamp
                        ? entry.timestamp.replace("T", " ").slice(0, 19) + " UTC"
                        : "";
                      const outcome = entry.status || "dispatched";
                      return h(
                        "div",
                        {
                          key: idx,
                          style: {
                            padding: "10px 0",
                            borderBottom: "1px solid var(--border)",
                          },
                        },
                        h(
                          "div",
                          {
                            style: {
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              gap: 8,
                            },
                          },
                          h(
                            "span",
                            { style: { fontSize: 12, fontWeight: 600 } },
                            (entry.repository || "unknown") +
                              " \xB7 " +
                              (entry.workflow_name || "workflow"),
                          ),
                          h(
                            "span",
                            {
                              className: "section-badge",
                              style: {
                                background:
                                  outcome === "dispatched"
                                    ? "rgba(63,185,80,0.15)"
                                    : "rgba(248,81,73,0.15)",
                                color:
                                  outcome === "dispatched"
                                    ? "var(--accent-green)"
                                    : "var(--accent-red)",
                              },
                            },
                            outcome,
                          ),
                        ),
                        h(
                          "div",
                          {
                            style: {
                              marginTop: 3,
                              fontSize: 11,
                              color: "var(--text-muted)",
                            },
                          },
                          ts +
                            (entry.provider ? " \xB7 " + entry.provider : "") +
                            (entry.branch ? " \xB7 " + entry.branch : "") +
                            (entry.run_id ? " \xB7 #" + entry.run_id : ""),
                        ),
                      );
                    }),
              ),
            ),
            h(
              "div",
              { className: "section", style: { marginTop: 16 } },
              h(
                "div",
                { className: "section-header" },
                h(
                  "span",
                  { className: "section-title" },
                  h(ActivityGlyph, { size: 14 }),
                  "Plan Preview",
                ),
              ),
              h(
                "div",
                { className: "section-body" },
                !plan
                  ? h(
                      "div",
                      {
                        style: { color: "var(--text-muted)", fontSize: 12 },
                      },
                      "Select a failed run above and click Preview.",
                    )
                  : [
                      h(
                        "div",
                        {
                          key: "summary",
                          style: {
                            display: "flex",
                            gap: 8,
                            alignItems: "center",
                            flexWrap: "wrap",
                            marginBottom: 12,
                          },
                        },
                        h(
                          "span",
                          {
                            className: "section-badge",
                            style: {
                              background: accepted
                                ? "rgba(63,185,80,0.15)"
                                : "rgba(248,81,73,0.15)",
                              color: accepted
                                ? "var(--accent-green)"
                                : "var(--accent-red)",
                            },
                          },
                          accepted ? "dispatch allowed" : "blocked",
                        ),
                        h(
                          "span",
                          {
                            style: {
                              fontSize: 12,
                              color: "var(--text-secondary)",
                            },
                          },
                          plan.decision && plan.decision.reason
                            ? plan.decision.reason
                            : "",
                        ),
                      ),
                      h(
                        "div",
                        {
                          key: "attempts",
                          style: {
                            display: "grid",
                            gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
                            gap: 8,
                            marginBottom: 12,
                          },
                        },
                        h(
                          "div",
                          { className: "stat-card", style: { padding: 10 } },
                          h("div", { className: "stat-label" }, "Attempts"),
                          h(
                            "div",
                            { style: { fontSize: 16, fontWeight: 700 } },
                            (plan.decision && plan.decision.attempt_count) || 0,
                          ),
                        ),
                        h(
                          "div",
                          { className: "stat-card", style: { padding: 10 } },
                          h("div", { className: "stat-label" }, "Remaining"),
                          h(
                            "div",
                            { style: { fontSize: 16, fontWeight: 700 } },
                            plan.decision &&
                              plan.decision.remaining_attempts != null
                              ? plan.decision.remaining_attempts
                              : "-",
                          ),
                        ),
                        h(
                          "div",
                          { className: "stat-card", style: { padding: 10 } },
                          h("div", { className: "stat-label" }, "Provider"),
                          h(
                            "div",
                            { style: { fontSize: 16, fontWeight: 700 } },
                            (plan.decision && plan.decision.provider_id) ||
                              provider,
                          ),
                        ),
                      ),
                      h("pre", {
                        key: "prompt",
                        style: {
                          margin: 0,
                          padding: 12,
                          background: "var(--bg-secondary)",
                          border: "1px solid var(--border)",
                          borderRadius: 8,
                          color: "var(--text-secondary)",
                          fontSize: 12,
                          whiteSpace: "pre-wrap",
                          maxHeight: 280,
                          overflow: "auto",
                        },
                        children:
                          (plan.decision && plan.decision.prompt_preview) ||
                          "(no prompt preview returned)",
                      }),
                      dispatchState
                        ? h(
                            "div",
                            {
                              key: "dispatch",
                              style: {
                                marginTop: 12,
                                fontSize: 12,
                                color: dispatchState.error
                                  ? "var(--accent-red)"
                                  : "var(--accent-green)",
                              },
                            },
                            dispatchState.error || dispatchState.note,
                          )
                        : null,
                    ],
              ),
            ),
            julesDispatchMsg
              ? h(
                  "div",
                  {
                    role: "alert",
                    style: {
                      margin: "12px 0 0",
                      padding: "10px 16px",
                      borderRadius: 6,
                      background:
                        julesDispatchMsg.type === "error"
                          ? "rgba(248,81,73,0.15)"
                          : "rgba(63,185,80,0.15)",
                      color:
                        julesDispatchMsg.type === "error"
                          ? "var(--accent-red)"
                          : "var(--accent-green)",
                      border:
                        "1px solid " +
                        (julesDispatchMsg.type === "error"
                          ? "var(--accent-red)"
                          : "var(--accent-green)"),
                      fontSize: 13,
                    },
                  },
                  julesDispatchMsg.text,
                )
              : null,
            h(
              "div",
              { className: "section", style: { marginTop: 16 } },
              h(
                "div",
                { className: "section-header" },
                h(
                  "span",
                  { className: "section-title" },
                  h(ClockGlyph, { size: 14 }),
                  "Jules Workflow Health",
                ),
              ),
              h(
                "div",
                { className: "section-body" },
                workflows.control_tower_summary
                  ? h(
                      "div",
                      {
                        style: {
                          marginBottom: 12,
                          padding: "10px 12px",
                          borderRadius: 8,
                          background: "rgba(210,153,34,0.15)",
                          color: "var(--accent-yellow)",
                          fontSize: 12,
                        },
                      },
                      workflows.control_tower_summary,
                    )
                  : null,
                ((workflows.workflows || []).length === 0
                  ? [
                      h(
                        "div",
                        {
                          key: "empty",
                          style: {
                            color: "var(--text-muted)",
                            fontSize: 12,
                          },
                        },
                        "No Jules workflow health data loaded yet.",
                      ),
                    ]
                  : workflows.workflows
                ).map(function (entry: any) {
                  if (entry.workflow_file) {
                    const ghActionsLink =
                      "https://github.com/D-sorganization/Repository_Management/actions/workflows/" +
                      entry.workflow_file;
                    const triggerType = entry.trigger_type || "dormant";
                    const triggerColor =
                      triggerType === "manual"
                        ? "var(--accent-blue)"
                        : triggerType === "scheduled"
                          ? "var(--accent-purple)"
                          : triggerType === "workflow_run"
                            ? "var(--text-secondary)"
                            : "var(--accent-yellow)";
                    const triggerBg =
                      triggerType === "manual"
                        ? "rgba(88,166,255,0.15)"
                        : triggerType === "scheduled"
                          ? "rgba(163,113,247,0.15)"
                          : triggerType === "workflow_run"
                            ? "rgba(139,148,158,0.15)"
                            : "rgba(227,179,65,0.15)";
                    const ghLink =
                      "https://github.com/D-sorganization/Repository_Management/blob/main/.github/workflows/" +
                      entry.workflow_file;
                    return h(
                      "div",
                      {
                        key: entry.workflow_file,
                        style: {
                          padding: "10px 0",
                          borderBottom: "1px solid var(--border)",
                        },
                      },
                      h(
                        "div",
                        {
                          style: {
                            display: "flex",
                            justifyContent: "space-between",
                            gap: 12,
                            alignItems: "center",
                          },
                        },
                        h(
                          "a",
                          {
                            href: ghActionsLink,
                            target: "_blank",
                            rel: "noopener noreferrer",
                            style: {
                              fontSize: 13,
                              fontWeight: 600,
                              color: "var(--text-primary)",
                              textDecoration: "none",
                            },
                          },
                          entry.workflow_name,
                        ),
                        h(
                          "span",
                          {
                            style: {
                              display: "flex",
                              alignItems: "center",
                              gap: 6,
                            },
                          },
                          h(
                            "a",
                            {
                              href: ghLink,
                              target: "_blank",
                              rel: "noopener noreferrer",
                              style: {
                                fontSize: 13,
                                fontWeight: 600,
                                color: "var(--text-primary)",
                                textDecoration: "none",
                              },
                            },
                            entry.workflow_name,
                          ),
                          h(
                            "span",
                            {
                              className: "section-badge",
                              style: {
                                background: triggerBg,
                                color: triggerColor,
                              },
                            },
                            triggerType,
                          ),
                        ),
                        h(
                          "div",
                          {
                            style: {
                              display: "flex",
                              gap: 6,
                              alignItems: "center",
                            },
                          },
                          "manual dispatch: " +
                            String(entry.manual_dispatch) +
                            " \xB7 scheduled: " +
                            String(entry.scheduled) +
                            " \xB7 workflow_run: " +
                            String(entry.workflow_run_trigger),
                          h(
                            "span",
                            {
                              className: "section-badge",
                              style: {
                                background:
                                  (entry.issues || []).length > 0
                                    ? "rgba(248,81,73,0.15)"
                                    : "rgba(63,185,80,0.15)",
                                color:
                                  (entry.issues || []).length > 0
                                    ? "var(--accent-red)"
                                    : "var(--accent-green)",
                              },
                            },
                            (entry.issues || []).length > 0
                              ? (entry.issues || []).length + " issue(s)"
                              : "healthy",
                          ),
                          triggerType === "manual"
                            ? h(
                                "button",
                                {
                                  style: {
                                    fontSize: 11,
                                    padding: "2px 8px",
                                    borderRadius: 4,
                                    border: "1px solid #58a6ff",
                                    background: "rgba(88,166,255,0.1)",
                                    color: "var(--accent-blue)",
                                    cursor: "pointer",
                                  },
                                  onClick: function () {
                                    dispatchJulesWorkflow(
                                      entry.workflow_file,
                                      setJulesDispatchMsg,
                                    );
                                  },
                                },
                                "Run",
                              )
                            : null,
                        ),
                      ),
                      (entry.issues || []).map(function (
                        issue: any,
                        issueIndex: number,
                      ) {
                        return h(
                          "div",
                          {
                            key: entry.workflow_file + "-" + issueIndex,
                            style: {
                              marginTop: 6,
                              fontSize: 12,
                              color: "var(--text-secondary)",
                            },
                          },
                          issue,
                        );
                      }),
                    );
                  }
                  return entry;
                }),
              ),
            ),
          ),
        ),
      ),
    subTab === "prs" &&
      h(RemediationPRsSubTab, { principalName: p.principalName } as any),
    subTab === "issues" &&
      h(RemediationIssuesSubTab, { principalName: p.principalName } as any),
  );
}

export default RemediationTab;
