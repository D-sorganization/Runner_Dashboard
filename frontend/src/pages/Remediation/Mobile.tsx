import React, { useCallback, useEffect, useRef, useState } from "react";
import { SegmentedControl } from "../../primitives/SegmentedControl";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { useToast } from "../../primitives/Toaster";
import { BottomSheet } from "../../primitives/BottomSheet";

// -- Types ---------------------------------------------------------------------

interface AgentProvider {
  provider_id: string;
  label: string;
  execution_mode: string;
  dispatch_mode: string;
  notes: string;
  experimental: boolean;
  remote: boolean;
  editable: boolean;
}

interface ProviderAvailability {
  provider_id: string;
  available: boolean;
  status: string;
  detail: string;
}

interface FailedRun {
  id: number;
  name: string;
  workflow_name: string;
  head_branch: string;
  conclusion: string;
  html_url: string;
  created_at: string;
  run_number?: number;
  repository: { name: string; full_name?: string };
}

interface OpenPR {
  id: number;
  number: number;
  title: string;
  html_url: string;
  head: { ref: string };
  base: { repo: { name: string; full_name?: string } };
  draft: boolean;
  labels: Array<{ name: string }>;
  updated_at: string;
}

interface OpenIssue {
  id: number;
  number: number;
  title: string;
  html_url: string;
  repository_url: string;
  labels: Array<{ name: string }>;
  updated_at: string;
}

type RemediationSubtab = "automations" | "prs" | "issues";

export interface InFlightDispatch {
  id: string;
  itemId: number;
  itemTitle: string;
  provider: string;
  providerLabel: string;
  repository: string;
  startedAt: number;
  lastHeartbeat: number;
  status: "dispatched" | "running" | "done" | "error";
  fingerprint?: string;
}

// -- Constants -----------------------------------------------------------------

const SUBTAB_OPTIONS = [
  { label: "Automations", value: "automations" },
  { label: "PRs", value: "prs" },
  { label: "Issues", value: "issues" },
];

const DEFAULT_PROVIDER_ORDER = [
  "jules_api",
  "codex_cli",
  "claude_code_cli",
  "gemini_cli",
  "ollama",
  "cline",
];

// -- Helpers -------------------------------------------------------------------

function pickRecommendedProvider(
  providers: Record<string, AgentProvider>,
  availability: Record<string, ProviderAvailability>,
): string {
  for (const id of DEFAULT_PROVIDER_ORDER) {
    if (providers[id] && availability[id]?.available) return id;
  }
  const first = Object.keys(providers).find((id) => availability[id]?.available);
  return first ?? "claude_code_cli";
}

function getProviderLabel(
  providers: Record<string, AgentProvider>,
  providerId: string,
): string {
  return providers[providerId]?.label ?? providerId;
}

function elapsedLabel(startedAt: number): string {
  const secs = Math.floor((Date.now() - startedAt) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

// -- Sub-components ------------------------------------------------------------

interface InFlightTileProps {
  dispatch: InFlightDispatch;
}

function InFlightTile({ dispatch }: InFlightTileProps) {
  const [, forceUpdate] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    intervalRef.current = setInterval(() => forceUpdate((n) => n + 1), 5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const tone =
    dispatch.status === "done"
      ? "success"
      : dispatch.status === "error"
      ? "danger"
      : "info";

  return (
    <div
      aria-label={`In-flight dispatch: ${dispatch.itemTitle}`}
      className="remediation-inflight-tile"
      role="status"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderLeft: "4px solid var(--accent-blue)",
        borderRadius: 10,
        marginBottom: 10,
        padding: "12px 14px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <span style={{ color: "var(--text-primary)", fontWeight: 600, fontSize: 13 }}>
          {dispatch.itemTitle}
        </span>
        <Badge tone={tone}>{dispatch.status}</Badge>
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: 12, display: "flex", gap: 10, flexWrap: "wrap" }}>
        <span>Agent: {dispatch.providerLabel}</span>
        <span>Repo: {dispatch.repository}</span>
        <span>Elapsed: {elapsedLabel(dispatch.startedAt)}</span>
        <span>Heartbeat: {elapsedLabel(dispatch.lastHeartbeat)} ago</span>
      </div>
    </div>
  );
}

interface ActionSheetProps {
  isOpen: boolean;
  onClose: () => void;
  itemTitle: string;
  itemHtmlUrl: string;
  recommendedProviderId: string;
  providers: Record<string, AgentProvider>;
  availability: Record<string, ProviderAvailability>;
  onDispatch: (providerId: string) => void;
  dispatching: boolean;
}

function ActionSheet({
  isOpen,
  onClose,
  itemTitle,
  itemHtmlUrl,
  recommendedProviderId,
  providers,
  availability,
  onDispatch,
  dispatching,
}: ActionSheetProps) {
  const [showAgentPicker, setShowAgentPicker] = useState(false);

  const handleOpenDesktop = useCallback(() => {
    window.open(itemHtmlUrl, "_blank", "noopener,noreferrer");
    onClose();
  }, [itemHtmlUrl, onClose]);

  const sortedProviders = Object.entries(providers)
    .map(([id, p]) => ({
      id,
      label: p.label ?? id,
      available: availability[id]?.available ?? false,
    }))
    .sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1;
      const ai = DEFAULT_PROVIDER_ORDER.indexOf(a.id);
      const bi = DEFAULT_PROVIDER_ORDER.indexOf(b.id);
      if (ai !== -1 && bi !== -1) return ai - bi;
      if (ai !== -1) return -1;
      if (bi !== -1) return 1;
      return a.label.localeCompare(b.label);
    });

  return (
    <>
      <BottomSheet isOpen={isOpen && !showAgentPicker} onClose={onClose} title={itemTitle}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <TouchButton
            aria-label={`Dispatch ${getProviderLabel(providers, recommendedProviderId)} for ${itemTitle}`}
            disabled={dispatching || !(availability[recommendedProviderId]?.available)}
            onClick={() => onDispatch(recommendedProviderId)}
            variant="primary"
            style={{ width: "100%", minHeight: 48, fontSize: 15 }}
          >
            {dispatching
              ? "Dispatching..."
              : `Dispatch ${getProviderLabel(providers, recommendedProviderId)}`}
          </TouchButton>

          <TouchButton
            aria-label="Pick a different agent"
            disabled={dispatching}
            onClick={() => setShowAgentPicker(true)}
            variant="default"
            style={{ width: "100%", minHeight: 48 }}
          >
            Pick agent...
          </TouchButton>

          <TouchButton
            aria-label="Open on desktop in new tab"
            onClick={handleOpenDesktop}
            variant="default"
            style={{ width: "100%", minHeight: 48 }}
          >
            Open on desktop
          </TouchButton>
        </div>
      </BottomSheet>

      <BottomSheet
        isOpen={isOpen && showAgentPicker}
        onClose={() => setShowAgentPicker(false)}
        title="Pick agent"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {sortedProviders.map(({ id, label, available }) => (
            <TouchButton
              key={id}
              aria-label={`Dispatch ${label}`}
              disabled={dispatching || !available}
              onClick={() => {
                setShowAgentPicker(false);
                onDispatch(id);
              }}
              variant={id === recommendedProviderId ? "primary" : "default"}
              style={{ width: "100%", minHeight: 44, display: "flex", justifyContent: "space-between", alignItems: "center" }}
            >
              <span>{label}</span>
              <Badge tone={available ? "success" : "danger"} size="sm">
                {available ? "Ready" : "Unavailable"}
              </Badge>
            </TouchButton>
          ))}
        </div>
      </BottomSheet>
    </>
  );
}

// -- Main component ------------------------------------------------------------

export interface RemediationMobileProps {
  /** In-flight dispatches are kept at parent level for persistence across tab switches. */
  inFlightDispatches: InFlightDispatch[];
  onAddInFlight: (dispatch: InFlightDispatch) => void;
}

export function RemediationMobile({ inFlightDispatches, onAddInFlight }: RemediationMobileProps) {
  const { showToast } = useToast();

  const [subtab, setSubtab] = useState<RemediationSubtab>("automations");
  const [providers, setProviders] = useState<Record<string, AgentProvider>>({});
  const [availability, setAvailability] = useState<Record<string, ProviderAvailability>>({});
  const [failedRuns, setFailedRuns] = useState<FailedRun[]>([]);
  const [openPRs, setOpenPRs] = useState<OpenPR[]>([]);
  const [openIssues, setOpenIssues] = useState<OpenIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [actionSheetItem, setActionSheetItem] = useState<{
    id: number;
    title: string;
    htmlUrl: string;
    repository: string;
    workflowName?: string;
    branch?: string;
    runId?: number;
  } | null>(null);
  const [dispatching, setDispatching] = useState(false);

  const recommendedProviderId = pickRecommendedProvider(providers, availability);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [provResp, runsResp, prsResp, issuesResp] = await Promise.all([
        fetch("/api/agent-remediation/providers"),
        fetch("/api/runs?conclusion=failure&per_page=20"),
        fetch("/api/pulls?state=open&per_page=20"),
        fetch("/api/issues?state=open&per_page=20"),
      ]);

      if (!provResp.ok) throw new Error(`Providers HTTP ${provResp.status}`);
      const provData = await provResp.json();
      setProviders(provData.providers ?? {});
      setAvailability(provData.availability ?? {});

      if (runsResp.ok) {
        const runsData = await runsResp.json();
        setFailedRuns(
          (runsData.workflow_runs ?? []).filter(
            (r: FailedRun) => r.conclusion === "failure",
          ),
        );
      }

      if (prsResp.ok) {
        const prsData = await prsResp.json();
        setOpenPRs(Array.isArray(prsData) ? prsData : (prsData.items ?? []));
      }

      if (issuesResp.ok) {
        const issuesData = await issuesResp.json();
        setOpenIssues(Array.isArray(issuesData) ? issuesData : (issuesData.items ?? []));
      }
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "Failed to load remediation data";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleDispatch = useCallback(
    async (providerId: string) => {
      if (!actionSheetItem) return;
      setDispatching(true);
      try {
        const payload = {
          repository: actionSheetItem.repository,
          workflow_name: actionSheetItem.workflowName ?? "unknown",
          branch: actionSheetItem.branch ?? "main",
          failure_reason: `Mobile dispatch for ${actionSheetItem.title}`,
          log_excerpt: `Dispatched via mobile remediation flow. Item ID: ${actionSheetItem.id}`,
          run_id: actionSheetItem.runId,
          provider: providerId,
          dispatch_origin: "manual",
        };

        const resp = await fetch("/api/agent-remediation/dispatch", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          body: JSON.stringify(payload),
        });

        const data = await resp.json();
        if (!resp.ok) {
          throw new Error(data.detail ?? `Dispatch failed: HTTP ${resp.status}`);
        }

        const inflight: InFlightDispatch = {
          id: `${actionSheetItem.id}-${Date.now()}`,
          itemId: actionSheetItem.id,
          itemTitle: actionSheetItem.title,
          provider: providerId,
          providerLabel: getProviderLabel(providers, providerId),
          repository: actionSheetItem.repository,
          startedAt: Date.now(),
          lastHeartbeat: Date.now(),
          status: "dispatched",
          fingerprint: data.fingerprint,
        };
        onAddInFlight(inflight);

        showToast(
          data.note ?? `Dispatched ${getProviderLabel(providers, providerId)} for ${actionSheetItem.title}`,
          { variant: "success", title: "Dispatch submitted" },
        );
        setActionSheetItem(null);
      } catch (e: unknown) {
        const message = e instanceof Error ? e.message : "Dispatch failed";
        showToast(message, { variant: "error", title: "Dispatch failed" });
      } finally {
        setDispatching(false);
      }
    },
    [actionSheetItem, providers, onAddInFlight, showToast],
  );

  function renderAutomations() {
    const inflight = inFlightDispatches.filter((d) => d.status !== "done");
    return (
      <>
        {inflight.map((d) => (
          <InFlightTile key={d.id} dispatch={d} />
        ))}
        {failedRuns.length === 0 ? (
          <div
            aria-label="No failed runs"
            className="remediation-empty"
            style={{ color: "var(--text-muted)", padding: "32px 0", textAlign: "center" }}
          >
            No failed runs found.
          </div>
        ) : (
          failedRuns.map((run) => {
            const repoName = run.repository?.name ?? "repo";
            const isInflight = inFlightDispatches.some((d) => d.itemId === run.id);
            if (isInflight) return null;
            return (
              <button
                key={run.id}
                aria-label={`Failed run: ${run.name ?? run.workflow_name} in ${repoName}`}
                className="remediation-card"
                onClick={() =>
                  setActionSheetItem({
                    id: run.id,
                    title: `${repoName}: ${run.name ?? run.workflow_name}`,
                    htmlUrl: run.html_url,
                    repository: repoName,
                    workflowName: run.workflow_name ?? run.name,
                    branch: run.head_branch ?? "main",
                    runId: run.id,
                  })
                }
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  cursor: "pointer",
                  display: "block",
                  marginBottom: 10,
                  padding: "12px 14px",
                  textAlign: "left",
                  width: "100%",
                }}
                type="button"
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ color: "var(--text-primary)", fontWeight: 600, fontSize: 13 }}>
                    {repoName}: {run.name ?? run.workflow_name}
                  </span>
                  <Badge tone="danger" size="sm">failure</Badge>
                </div>
                <div style={{ color: "var(--text-secondary)", fontSize: 12, marginBottom: 4 }}>
                  {run.head_branch ?? "main"} · #{run.run_number ?? run.id}
                </div>
                <Badge tone="info" size="sm">
                  Recommended: {getProviderLabel(providers, recommendedProviderId)}
                </Badge>
              </button>
            );
          })
        )}
      </>
    );
  }

  function renderPRs() {
    return (
      <>
        {openPRs.length === 0 ? (
          <div
            aria-label="No open PRs"
            className="remediation-empty"
            style={{ color: "var(--text-muted)", padding: "32px 0", textAlign: "center" }}
          >
            No open PRs found.
          </div>
        ) : (
          openPRs.map((pr) => {
            const repoName = pr.base?.repo?.name ?? "repo";
            const isInflight = inFlightDispatches.some((d) => d.itemId === pr.id);
            if (isInflight) return null;
            return (
              <button
                key={pr.id}
                aria-label={`Open PR: ${pr.title} in ${repoName}`}
                className="remediation-card"
                onClick={() =>
                  setActionSheetItem({
                    id: pr.id,
                    title: `PR #${pr.number}: ${pr.title}`,
                    htmlUrl: pr.html_url,
                    repository: repoName,
                    workflowName: "pr-remediation",
                    branch: pr.head?.ref ?? "main",
                  })
                }
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  cursor: "pointer",
                  display: "block",
                  marginBottom: 10,
                  padding: "12px 14px",
                  textAlign: "left",
                  width: "100%",
                }}
                type="button"
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ color: "var(--text-primary)", fontWeight: 600, fontSize: 13 }}>
                    PR #{pr.number}: {pr.title}
                  </span>
                  {pr.draft && <Badge tone="neutral" size="sm">Draft</Badge>}
                </div>
                <div style={{ color: "var(--text-secondary)", fontSize: 12, marginBottom: 4 }}>
                  {repoName} · {pr.head?.ref ?? "unknown branch"}
                </div>
                <Badge tone="info" size="sm">
                  Recommended: {getProviderLabel(providers, recommendedProviderId)}
                </Badge>
              </button>
            );
          })
        )}
      </>
    );
  }

  function renderIssues() {
    return (
      <>
        {openIssues.length === 0 ? (
          <div
            aria-label="No open issues"
            className="remediation-empty"
            style={{ color: "var(--text-muted)", padding: "32px 0", textAlign: "center" }}
          >
            No open issues found.
          </div>
        ) : (
          openIssues.map((issue) => {
            const repoName = issue.repository_url?.split("/").pop() ?? "repo";
            const isInflight = inFlightDispatches.some((d) => d.itemId === issue.id);
            if (isInflight) return null;
            return (
              <button
                key={issue.id}
                aria-label={`Open issue: ${issue.title}`}
                className="remediation-card"
                onClick={() =>
                  setActionSheetItem({
                    id: issue.id,
                    title: `Issue #${issue.number}: ${issue.title}`,
                    htmlUrl: issue.html_url,
                    repository: repoName,
                    workflowName: "issue-remediation",
                    branch: "main",
                  })
                }
                style={{
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  borderRadius: 10,
                  cursor: "pointer",
                  display: "block",
                  marginBottom: 10,
                  padding: "12px 14px",
                  textAlign: "left",
                  width: "100%",
                }}
                type="button"
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ color: "var(--text-primary)", fontWeight: 600, fontSize: 13 }}>
                    #{issue.number}: {issue.title}
                  </span>
                </div>
                <div style={{ color: "var(--text-secondary)", fontSize: 12, marginBottom: 4 }}>
                  {repoName}
                  {issue.labels.length > 0 && (
                    <> {issue.labels.map((l) => l.name).join(", ")}</>
                  )}
                </div>
                <Badge tone="info" size="sm">
                  Recommended: {getProviderLabel(providers, recommendedProviderId)}
                </Badge>
              </button>
            );
          })
        )}
      </>
    );
  }

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading remediation data"
        aria-live="polite"
        className="remediation-mobile-loading"
        role="status"
        style={{ padding: "16px", display: "flex", flexDirection: "column", gap: 12 }}
      >
        <SkeletonLine height={20} width="60%" />
        <SkeletonLine height={36} width="100%" />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  if (error) {
    return (
      <div
        aria-live="assertive"
        className="remediation-mobile-error"
        role="alert"
        style={{ color: "var(--accent-red)", padding: "24px", textAlign: "center" }}
      >
        <div style={{ marginBottom: 12 }}>{error}</div>
        <TouchButton onClick={fetchData} variant="primary">
          Retry
        </TouchButton>
      </div>
    );
  }

  return (
    <section
      aria-label="Mobile remediation"
      className="remediation-mobile"
      style={{ padding: "12px 12px 24px" }}
    >
      <SegmentedControl
        ariaLabel="Remediation subtabs"
        onChange={(v) => setSubtab(v as RemediationSubtab)}
        options={SUBTAB_OPTIONS}
        value={subtab}
      />

      <div
        aria-live="polite"
        className="remediation-list"
        style={{ marginTop: 14 }}
      >
        {subtab === "automations" && renderAutomations()}
        {subtab === "prs" && renderPRs()}
        {subtab === "issues" && renderIssues()}
      </div>

      {actionSheetItem && (
        <ActionSheet
          isOpen={true}
          onClose={() => !dispatching && setActionSheetItem(null)}
          itemTitle={actionSheetItem.title}
          itemHtmlUrl={actionSheetItem.htmlUrl}
          recommendedProviderId={recommendedProviderId}
          providers={providers}
          availability={availability}
          onDispatch={handleDispatch}
          dispatching={dispatching}
        />
      )}
    </section>
  );
}
