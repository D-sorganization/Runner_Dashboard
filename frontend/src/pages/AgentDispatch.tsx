import React, { useCallback, useEffect, useMemo, useState } from "react"
import { TouchButton } from "../primitives/TouchButton";

/**
 * Agent Dispatch Page — Mobile Remediation + 3-tap Agent Dispatch flow
 * Issue #196 [M10]
 *
 * 3-tap confirmation flow:
 *   1. Select agent
 *   2. Review dispatch details
 *   3. Confirm dispatch
 */

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

const DEFAULT_PROVIDER_ORDER = [
  "jules_api",
  "codex_cli",
  "claude_code_cli",
  "gemini_cli",
  "ollama",
  "cline",
];

export function AgentDispatchPage() {
  const [providers, setProviders] = useState<Record<string, AgentProvider>>({});
  const [availability, setAvailability] = useState<Record<string, ProviderAvailability>>({});
  const [failedRuns, setFailedRuns] = useState<FailedRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [step, setStep] = useState<DispatchStep>("select");
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<FailedRun | null>(null);
  const [dispatching, setDispatching] = useState(false);
  const [dispatchResult, setDispatchResult] = useState<{
    status: "success" | "error";
    message: string;
  } | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const provResp = await fetch("/api/agent-remediation/providers");
      if (!provResp.ok) throw new Error(`Providers HTTP ${provResp.status}`);
      const provData = await provResp.json();
      setProviders(provData.providers || {});
      setAvailability(provData.availability || {});

      const runsResp = await fetch("/api/runs?per_page=30");
      if (!runsResp.ok) throw new Error(`Runs HTTP ${runsResp.status}`);
      const runsData = await runsResp.json();
      setFailedRuns(
        (runsData.workflow_runs || []).filter(
          (r: FailedRun) => r.conclusion === "failure"
        )
      );
    } catch (e: any) {
      setError(e.message || "Failed to load dispatch data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const availableProviders = useMemo(() => {
    const entries = Object.entries(providers);
    if (entries.length === 0) {
      return DEFAULT_PROVIDER_ORDER.map((id) => ({
        id,
        label: id,
        available: false,
        status: "loading",
        detail: "",
        experimental: false,
        notes: "",
      }));
    }
    return entries
      .map(([id, provider]) => {
        const avail = availability[id];
        return {
          id,
          label: provider.label || id,
          available: avail?.available ?? false,
          status: avail?.status || "unknown",
          detail: avail?.detail || "",
          experimental: provider.experimental ?? false,
          notes: provider.notes || "",
        };
      })
      .sort((a, b) => {
        const aAvail = a.available ? 1 : 0;
        const bAvail = b.available ? 1 : 0;
        if (aAvail !== bAvail) return bAvail - aAvail;
        const aIndex = DEFAULT_PROVIDER_ORDER.indexOf(a.id);
        const bIndex = DEFAULT_PROVIDER_ORDER.indexOf(b.id);
        if (aIndex !== -1 && bIndex !== -1) return aIndex - bIndex;
        if (aIndex !== -1) return -1;
        if (bIndex !== -1) return 1;
        return a.label.localeCompare(b.label);
      });
  }, [providers, availability]);

  const selectedProvider = useMemo(() => {
    if (!selectedProviderId) return null;
    return availableProviders.find((p) => p.id === selectedProviderId) || null;
  }, [selectedProviderId, availableProviders]);

  function selectProvider(providerId: string) {
    setSelectedProviderId(providerId);
    setStep("review");
    setDispatchResult(null);
  }

  function selectRun(run: FailedRun) {
    setSelectedRun(run);
    if (step === "select") setStep("review");
  }

  function goBack() {
    if (step === "review") {
      setStep("select");
      setSelectedProviderId(null);
      setSelectedRun(null);
    } else if (step === "dispatch") {
      setStep("review");
      setDispatchResult(null);
    }
  }

  async function confirmDispatch() {
    if (!selectedProviderId || !selectedRun) return;
    setStep("dispatch");
    setDispatching(true);
    setDispatchResult(null);
    try {
      const repoName = selectedRun.repository?.name || "unknown";
      const payload = {
        repository: repoName,
        workflow_name: selectedRun.workflow_name || selectedRun.name || "unknown",
        branch: selectedRun.head_branch || "main",
        failure_reason: `Dispatching ${selectedProviderId} for failed run #${selectedRun.id}`,
        log_excerpt: `Run ${selectedRun.id} concluded with failure. Dispatched via mobile agent dispatch flow.`,
        run_id: selectedRun.id,
        provider: selectedProviderId,
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
      setDispatchResult({
        status: "success",
        message:
          data.note ||
          `Dispatch submitted for ${selectedProviderId} on ${repoName}. Waiting for agent heartbeat.`,
      });
    } catch (e: any) {
      setDispatchResult({
        status: "error",
        message: e.message || "Dispatch failed. Please try again.",
      });
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

  function renderProviderCard(provider: typeof availableProviders[number]) {
    const isSelected = selectedProviderId === provider.id;
    const isAvailable = provider.available;
    return (
      <button key={provider.id} aria-pressed={isSelected} data-touch-primitive="TouchButton" disabled={!isAvailable} onClick={() => selectProvider(provider.id)} className={`provider-card ${isSelected ? "selected" : ""} ${isAvailable ? "" : "unavailable"}`}>
        <div className="provider-header">
          <span className="label">{provider.label}</span>
          <span className={`status ${isAvailable ? "ready" : "missing"}`}>{isAvailable ? "Ready" : provider.status}</span>
        </div>
        {provider.notes && <span className="notes">{provider.notes}</span>}
        {provider.experimental && <span className="experimental">Experimental</span>}
      </button>
    );
  }

  function renderSelectStep() {
    return (
      <section aria-label="Mobile remediation dispatch">
        <h2>Select Agent</h2>
        <p className="step-description">Choose an available agent to dispatch for CI remediation. Only agents marked Ready can be dispatched.</p>
        <div className="provider-list">{availableProviders.map(renderProviderCard)}</div>
        <h2>Failed Runs</h2>
        <p className="step-description">Tap a failed run to associate it with the dispatch (optional).</p>
        {failedRuns.length === 0 ? (
          <div className="empty-state">No failed runs found.</div>
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
        {selectedProviderId && <TouchButton onClick={() => setStep("review")} pressed={false} style={{ width: "100%" }} variant="primary">Review Dispatch →</TouchButton>}
      </section>
    );
  }

  function renderReviewStep() {
    const provider = selectedProvider;
    const run = selectedRun;
    return (
      <section aria-label="Confirm mobile credential change">
        <h2>Review Dispatch</h2>
        <p className="step-description">Preview the safety plan before dispatching the agent.</p>
        <div className="review-cards">
          <div className="review-card">
            <div className="label">Agent</div>
            <div className="value">{provider?.label || selectedProviderId}</div>
            {provider?.available ? <div className="available">Available — {provider.detail || "ready"}</div> : <div className="unavailable">Not available — {provider?.detail || provider?.status || "unknown"}</div>}
          </div>
          {run ? (
            <div className="review-card">
              <div className="label">Target Run</div>
              <div className="value">{run.repository?.name || "repo"} · {run.name || run.workflow_name} #{run.id}</div>
              <div className="meta">Branch {run.head_branch || "main"} · Run #{run.run_number || run.id}</div>
            </div>
          ) : <div className="review-card dashed">No run selected — dispatch will target the default workflow.</div>}
          <div className="safety-plan"><strong>Safety Plan Preview:</strong> The agent will attempt a minimal, safe fix. Protected branches require PR-based remediation. Loop guards prevent infinite retry cycles.</div>
        </div>
        <div className="action-buttons">
          <TouchButton disabled={!provider?.available || dispatching} onClick={confirmDispatch} style={{ width: "100%" }} variant="primary">
            {dispatching ? <span className="dispatching-spinner">Dispatching…</span> : "Confirm Dispatch"}
          </TouchButton>
          <TouchButton onClick={goBack} style={{ width: "100%" }} variant="default">← Back to Selection</TouchButton>
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
              <TouchButton onClick={() => { setStep("select"); setSelectedProviderId(null); setSelectedRun(null); setDispatchResult(null); }} style={{ width: "100%" }} variant="primary">New Dispatch</TouchButton>
              <TouchButton onClick={goBack} style={{ width: "100%" }} variant="default">Back to Review</TouchButton>
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

  if (loading) return <div aria-live="polite" style={{ padding: "24px", textAlign: "center", color: "var(--text-muted)", fontSize: "14px" }}>Loading dispatch data…</div>;
  if (error && !loading) return <div aria-live="assertive" role="alert" style={{ padding: "24px", textAlign: "center", color: "var(--accent-red)", fontSize: "14px" }}><div style={{ marginBottom: "12px" }}>{error}</div><TouchButton onClick={fetchData} variant="primary">Retry</TouchButton></div>;

  return (
    <div className="agent-dispatch-page">
      {renderStepIndicator()}
      {step === "select" && renderSelectStep()}
      {step === "review" && renderReviewStep()}
      {step === "dispatch" && renderDispatchStep()}
    </div>
  );
}
