import React, { useCallback, useEffect, useMemo, useState } from "react"
import { EmptyState } from "../primitives/EmptyState";
import { TouchButton } from "../primitives/TouchButton";
import { useToast } from "../primitives/Toaster";
import { SkeletonCard, SkeletonLine } from "../primitives/Skeleton";
import {
  ProviderModelSelector,
  type ProviderModelSelection,
} from "../primitives/ProviderModelSelector";
import { useProviderRegistry } from "../lib/useProviderRegistry";

/**
 * Agent Dispatch Page — Mobile Remediation + 3-tap Agent Dispatch flow
 * Issue #196 [M10]; refactored onto the unified provider registry (#811).
 *
 * 3-tap confirmation flow:
 *   1. Select agent (provider) + model via the shared ProviderModelSelector
 *   2. Review dispatch details
 *   3. Confirm dispatch
 *
 * DRY: providers come from the single source of truth
 * (GET /api/providers/registry via useProviderRegistry). The old hardcoded
 * DEFAULT_PROVIDER_ORDER list has been removed (#809). The selected MODEL flows
 * through to the dispatch payload; the dispatch `provider` uses the provider's
 * dashboard id so the backend remediation contract is unchanged.
 */

interface FailedRun {
  id: number;
  repository: { name: string };
  name: string;
  workflow_name: string;
  head_branch: string;
  conclusion: string;
  html_url: string;
  created_at: string;
  run_number?: number;
}

type DispatchStep = "select" | "review" | "dispatch";

const EMPTY_SELECTION: ProviderModelSelection = {
  providerId: null,
  dashboardId: null,
  model: null,
};

export function AgentDispatchPage() {
  const { showToast } = useToast();
  const { registry, loading: registryLoading, error: registryError } = useProviderRegistry();
  const [failedRuns, setFailedRuns] = useState<FailedRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);

  const [step, setStep] = useState<DispatchStep>("select");
  const [selection, setSelection] = useState<ProviderModelSelection>(EMPTY_SELECTION);
  const [selectedRun, setSelectedRun] = useState<FailedRun | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [dispatchResult, setDispatchResult] = useState<{
    status: "success" | "error";
    message: string;
  } | null>(null);

  const fetchRuns = useCallback(async () => {
    setRunsLoading(true);
    setRunsError(null);
    try {
      const runsResp = await fetch("/api/runs?per_page=30");
      if (!runsResp.ok) throw new Error(`Runs HTTP ${runsResp.status}`);
      const runsData = await runsResp.json();
      setFailedRuns(
        (runsData.workflow_runs || []).filter(
          (r: FailedRun) => r.conclusion === "failure",
        ),
      );
    } catch (e) {
      setRunsError((e instanceof Error ? e.message : String(e)) || "Failed to load runs");
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
  }, [fetchRuns]);

  const loading = registryLoading || runsLoading;
  const error = registryError || runsError;

  const selectedProvider = useMemo(
    () => (registry && selection.providerId ? registry.byId(selection.providerId) ?? null : null),
    [registry, selection.providerId],
  );

  const handleSelectionChange = useCallback((next: ProviderModelSelection) => {
    setSelection(next);
    setDispatchResult(null);
  }, []);

  const handleRequestLogin = useCallback(() => {
    // Navigate to the Credentials surface to fix a provider's login (#812).
    try {
      window.location.assign("/?tab=credentials");
    } catch {
      /* navigation unavailable in test env — non-fatal */
    }
  }, []);

  function selectRun(run: FailedRun) {
    setSelectedRun(run);
  }

  function goBack() {
    if (step === "review") {
      setStep("select");
    } else if (step === "dispatch") {
      setStep("review");
      setDispatchResult(null);
    }
  }

  async function confirmDispatch() {
    if (!selectedProvider || !selectedRun) return;
    setStep("dispatch");
    setDispatching(true);
    setDispatchResult(null);
    try {
      const repoName = selectedRun.repository?.name || "unknown";
      const payload = {
        repository: repoName,
        workflow_name: selectedRun.workflow_name || selectedRun.name || "unknown",
        branch: selectedRun.head_branch || "main",
        failure_reason: `Dispatching ${selectedProvider.label} for failed run #${selectedRun.id}`,
        log_excerpt: `Run ${selectedRun.id} concluded with failure. Dispatched via mobile agent dispatch flow.`,
        run_id: selectedRun.id,
        // Backend remediation contract keys on the dashboard (underscored) id.
        provider: selectedProvider.dashboardId,
        // Selected model flows through; null when the provider has no models.
        model: selection.model,
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
      if (!resp.ok) throw new Error(data.detail || `Dispatch failed: HTTP ${resp.status}`);
      const successMessage =
        data.note ||
        `Dispatch submitted for ${selectedProvider.label} on ${repoName}. Waiting for agent heartbeat.`;
      setDispatchResult({ status: "success", message: successMessage });
      showToast(successMessage, { variant: "success", title: "Dispatch submitted" });
    } catch (e) {
      const errorMessage = (e instanceof Error ? e.message : String(e)) || "Dispatch failed. Please try again.";
      setDispatchResult({ status: "error", message: errorMessage });
      showToast(errorMessage, { variant: "error", title: "Dispatch failed" });
    } finally {
      setDispatching(false);
    }
  }

  function renderStepIndicator() {
    const steps: { key: DispatchStep; label: string }[] = [
      { key: "select", label: "Select" },
      { key: "review", label: "Review" },
      { key: "dispatch", label: "Dispatch" },
    ];
    const currentIndex = steps.findIndex((s) => s.key === step);
    return (
      <div aria-label="Dispatch step indicator" role="progressbar" aria-valuenow={currentIndex + 1} aria-valuemin={1} aria-valuemax={3} className="step-indicator">
        {steps.map((s, idx) => (
          <React.Fragment key={s.key}>
            <div className={`step-bubble ${idx <= currentIndex ? "active" : ""}`}>{idx + 1}</div>
            <span className={`step-label ${idx <= currentIndex ? "active" : ""}`}>{s.label}</span>
            {idx < steps.length - 1 && <div className={`step-line ${idx < currentIndex ? "active" : ""}`} />}
          </React.Fragment>
        ))}
      </div>
    );
  }

  function renderSelectStep() {
    const canReview = !!selectedProvider;
    return (
      <section aria-label="Mobile remediation dispatch">
        <h2>Select Agent</h2>
        <p className="step-description">
          Choose a provider and (where supported) a model to dispatch for CI remediation.
        </p>
        {registry && (
          <ProviderModelSelector
            registry={registry}
            value={selection}
            onChange={handleSelectionChange}
            onRequestLogin={handleRequestLogin}
            idPrefix="dispatch"
          />
        )}
        <h2>Failed Runs</h2>
        <p className="step-description">Tap a failed run to associate it with the dispatch.</p>
        {failedRuns.length === 0 ? (
          <EmptyState title="No failed runs found" description="Recent workflow runs do not need manual agent dispatch." />
        ) : (
          <div className="run-list">
            {failedRuns.map((run) => {
              const isSelected = selectedRun?.id === run.id;
              const repoName = run.repository?.name || "repo";
              return (
                <button key={run.id} aria-pressed={isSelected} data-touch-primitive="TouchButton" onClick={() => selectRun(run)} className={`run-card ${isSelected ? "selected" : ""}`}>
                  <div className="title">{repoName} · {run.name || run.workflow_name} #{run.id}</div>
                  <div className="meta">{run.head_branch || "main"} · {run.created_at ? run.created_at.replace("T", " ").slice(0, 19) + " UTC" : "—"}</div>
                </button>
              );
            })}
          </div>
        )}
        {canReview && <TouchButton className="agent-dispatch__full-width-action" onClick={() => setStep("review")} pressed={false} variant="primary">Review Dispatch →</TouchButton>}
      </section>
    );
  }

  function renderReviewStep() {
    const run = selectedRun;
    const authed = selectedProvider?.loginStatus === "authenticated";
    return (
      <section aria-label="Confirm dispatch">
        <h2>Review Dispatch</h2>
        <p className="step-description">Preview the safety plan before dispatching the agent.</p>
        <div className="review-cards">
          <div className="review-card">
            <div className="label">Agent</div>
            <div className="value">
              {selectedProvider?.label || "—"}
              {selection.model ? ` · ${selection.model}` : ""}
            </div>
            {authed ? <div className="available">Authenticated</div> : <div className="unavailable">Login required — {selectedProvider?.loginStatus || "unknown"}</div>}
          </div>
          {run ? (
            <div className="review-card">
              <div className="label">Target Run</div>
              <div className="value">{run.repository?.name || "repo"} · {run.name || run.workflow_name} #{run.id}</div>
              <div className="meta">Branch {run.head_branch || "main"} · Run #{run.run_number || run.id}</div>
            </div>
          ) : <div className="review-card dashed">No run selected — select a failed run first.</div>}
          <div className="safety-plan"><strong>Safety Plan Preview:</strong> The agent will attempt a minimal, safe fix. Protected branches require PR-based remediation. Loop guards prevent infinite retry cycles.</div>
        </div>
        <div className="action-buttons">
          <TouchButton className="agent-dispatch__full-width-action" disabled={!selectedProvider || !run || dispatching} onClick={confirmDispatch} variant="primary">
            {dispatching ? <span className="dispatching-spinner">Dispatching…</span> : "Confirm Dispatch"}
          </TouchButton>
          <TouchButton className="agent-dispatch__full-width-action" onClick={goBack} variant="default">← Back to Selection</TouchButton>
        </div>
      </section>
    );
  }

  function renderDispatchStep() {
    if (dispatchResult) {
      const isSuccess = dispatchResult.status === "success";
      return (
        <section aria-label="Dispatch result">
          <div className="dispatch-result">
            <div className={`result-icon ${isSuccess ? "success" : "error"}`}>{isSuccess ? "✓" : "✗"}</div>
            <h2>{isSuccess ? "Dispatch Submitted" : "Dispatch Failed"}</h2>
            <p>{dispatchResult.message}</p>
            <div className="action-buttons">
              <TouchButton className="agent-dispatch__full-width-action" onClick={() => { setStep("select"); setSelection(EMPTY_SELECTION); setSelectedRun(null); setDispatchResult(null); }} variant="primary">New Dispatch</TouchButton>
              <TouchButton className="agent-dispatch__full-width-action" onClick={goBack} variant="default">Back to Review</TouchButton>
            </div>
          </div>
        </section>
      );
    }
    return (
      <section aria-label="Dispatching agent">
        <div className="dispatch-loading">
          <div className="spinner" />
          <h2>Dispatching Agent…</h2>
          <p>Waiting for agent heartbeat.</p>
        </div>
      </section>
    );
  }

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-live="polite"
        className="agent-dispatch-loading"
        role="status"
      >
        <span className="visually-hidden">Loading dispatch data…</span>
        <SkeletonLine height={20} width="50%" />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
      </div>
    );
  }
  if (error && !loading) {
    return (
      <div className="agent-dispatch-error">
        <EmptyState variant="error" title="Failed to load dispatch data" description={error} onRetry={fetchRuns} />
      </div>
    );
  }

  return (
    <div className="agent-dispatch-page">
      {renderStepIndicator()}
      {step === "select" && renderSelectStep()}
      {step === "review" && renderReviewStep()}
      {step === "dispatch" && renderDispatchStep()}
    </div>
  );
}
