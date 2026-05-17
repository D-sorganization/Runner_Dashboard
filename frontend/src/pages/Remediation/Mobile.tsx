import { useCallback, useEffect, useState } from "react";
import { SegmentedControl } from "../../primitives/SegmentedControl";
import { TouchButton } from "../../primitives/TouchButton";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { useToast } from "../../primitives/Toaster";

import { ActionSheet } from "./ActionSheet";
import { AutomationsList, IssuesList, PRsList } from "./RemediationLists";
import type {
  ActionSheetItem,
  AgentProvider,
  FailedRun,
  InFlightDispatch,
  OpenIssue,
  OpenPR,
  ProviderAvailability,
  RemediationSubtab,
} from "./mobileTypes";
import {
  SUBTAB_OPTIONS,
  getProviderLabel,
  pickRecommendedProvider,
} from "./mobileTypes";

// Re-export InFlightDispatch for callers that imported it from this file pre-refactor.
export type { InFlightDispatch } from "./mobileTypes";

export interface RemediationMobileProps {
  /** In-flight dispatches are kept at parent level for persistence across tab switches. */
  inFlightDispatches: InFlightDispatch[];
  onAddInFlight: (dispatch: InFlightDispatch) => void;
}

export function RemediationMobile({
  inFlightDispatches,
  onAddInFlight,
}: RemediationMobileProps) {
  const { showToast } = useToast();

  const [subtab, setSubtab] = useState<RemediationSubtab>("automations");
  const [providers, setProviders] = useState<Record<string, AgentProvider>>({});
  const [availability, setAvailability] = useState<
    Record<string, ProviderAvailability>
  >({});
  const [failedRuns, setFailedRuns] = useState<FailedRun[]>([]);
  const [openPRs, setOpenPRs] = useState<OpenPR[]>([]);
  const [openIssues, setOpenIssues] = useState<OpenIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [actionSheetItem, setActionSheetItem] = useState<ActionSheetItem | null>(
    null,
  );
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
        setOpenIssues(
          Array.isArray(issuesData) ? issuesData : (issuesData.items ?? []),
        );
      }
    } catch (e: unknown) {
      const message =
        e instanceof Error ? e.message : "Failed to load remediation data";
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
          data.note ??
            `Dispatched ${getProviderLabel(providers, providerId)} for ${actionSheetItem.title}`,
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

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading remediation data"
        aria-live="polite"
        className="remediation-mobile-loading"
        role="status"
        style={{
          padding: "16px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
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
        style={{
          color: "var(--accent-red)",
          padding: "24px",
          textAlign: "center",
        }}
      >
        <div style={{ marginBottom: 12 }}>{error}</div>
        <TouchButton onClick={fetchData} variant="primary">
          Retry
        </TouchButton>
      </div>
    );
  }

  const commonListProps = {
    inFlightDispatches,
    providers,
    recommendedProviderId,
    onSelect: setActionSheetItem,
  };

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
        {subtab === "automations" && (
          <AutomationsList {...commonListProps} failedRuns={failedRuns} />
        )}
        {subtab === "prs" && (
          <PRsList {...commonListProps} openPRs={openPRs} />
        )}
        {subtab === "issues" && (
          <IssuesList {...commonListProps} openIssues={openIssues} />
        )}
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
