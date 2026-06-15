/* eslint-disable @typescript-eslint/no-explicit-any -- RemediationPage adapts dynamic remediation and workflow-run payloads into the 1:1 RemediationTab contract while #949 retires legacy/App.tsx. */
import React, { useCallback, useEffect, useState } from "react";
import { RemediationTab } from "./RemediationTab";
import { legacyFetch } from "../lib/api";

interface RemediationContext {
  repository: string;
  workflow_name: string;
  branch: string;
  run_id: string | number;
  failure_reason: string;
  protected_branch: boolean;
  attempts: any[];
}

function normalizeObject(payload: unknown): Record<string, any> {
  return payload && typeof payload === "object"
    ? (payload as Record<string, any>)
    : {};
}

function normalizeArrayPayload(payload: unknown, key: string): any[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object") {
    const value = (payload as Record<string, unknown>)[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function getJson(url: string, signal?: AbortSignal): Promise<unknown> {
  return legacyFetch(url, { signal }).then((response) => {
    if (!response.ok) throw new Error(url + " HTTP " + response.status);
    return response.json();
  });
}

function parseJsonOrThrow(response: Response, fallback: string): Promise<unknown> {
  return response.json().then((payload: unknown) => {
    if (!response.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload
          ? String((payload as { detail?: unknown }).detail)
          : fallback;
      throw new Error(detail);
    }
    return payload;
  });
}

function buildRemediationContext(run: any): RemediationContext | null {
  if (!run) return null;
  const branch = run.head_branch || "";
  const repository = run.repository && run.repository.name ? run.repository.name : "";
  const workflowName = run.name || run.workflow_name || "CI Standard";
  return {
    repository,
    workflow_name: workflowName,
    branch,
    run_id: run.id,
    failure_reason: workflowName + " failed for " + repository + " on " + branch,
    protected_branch: branch === "main" || branch === "master",
    attempts: [],
  };
}

export function RemediationPage(): React.ReactElement {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [workflows, setWorkflows] = useState<Record<string, any>>({});
  const [runs, setRuns] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [provider, setProvider] = useState("jules_api");
  const [model, setModel] = useState("");
  const [plan, setPlan] = useState<any>(null);
  const [dispatchState, setDispatchState] = useState<any>(null);

  const refreshHistory = useCallback((signal?: AbortSignal) => {
    return getJson("/api/agent-remediation/history", signal)
      .then((payload) => {
        setHistory(normalizeArrayPayload(payload, "history"));
      })
      .catch(() => {
        setHistory([]);
      });
  }, []);

  const refresh = useCallback(
    (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      Promise.all([
        getJson("/api/agent-remediation/config", signal),
        getJson("/api/agent-remediation/workflows", signal),
        getJson("/api/agent-remediation/history", signal).catch(() => ({
          history: [],
        })),
        getJson("/api/runs/enriched?per_page=50", signal).catch(() => ({
          runs: [],
        })),
        getJson("/api/runs?per_page=30", signal).catch(() => ({ runs: [] })),
      ])
        .then(([configPayload, workflowsPayload, historyPayload, enrichedRuns, rawRuns]) => {
          const nextConfig = normalizeObject(configPayload);
          const enriched = normalizeArrayPayload(enrichedRuns, "runs");
          const fallbackRuns = normalizeArrayPayload(rawRuns, "runs");
          setConfig(nextConfig);
          setWorkflows(normalizeObject(workflowsPayload));
          setHistory(normalizeArrayPayload(historyPayload, "history"));
          setRuns(enriched.length ? enriched : fallbackRuns);
          setProvider(
            nextConfig.policy && nextConfig.policy.default_provider
              ? String(nextConfig.policy.default_provider)
              : "jules_api",
          );
        })
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === "AbortError") return;
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load remediation controls from the dashboard backend.",
          );
        })
        .finally(() => {
          if (!signal?.aborted) setLoading(false);
        });
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const saveConfig = useCallback((policy: any) => {
    setLoading(true);
    return legacyFetch("/api/agent-remediation/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ policy }),
    })
      .then((response) => parseJsonOrThrow(response, "Save failed"))
      .then((payload) => {
        const nextConfig = normalizeObject(payload);
        setConfig(nextConfig);
        setProvider(
          nextConfig.policy && nextConfig.policy.default_provider
            ? String(nextConfig.policy.default_provider)
            : "jules_api",
        );
        setError(null);
        return nextConfig;
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Save failed");
        throw err;
      })
      .finally(() => setLoading(false));
  }, []);

  const preview = useCallback(
    (run: any) => {
      const context = buildRemediationContext(run);
      if (!context) {
        setError("Select a failed run before previewing remediation.");
        return;
      }
      setLoading(true);
      setDispatchState(null);
      legacyFetch("/api/agent-remediation/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...context,
          provider_override: provider,
          model_override: model || undefined,
        }),
      })
        .then((response) => parseJsonOrThrow(response, "Preview failed"))
        .then((payload) => {
          setPlan(payload);
          setError(null);
        })
        .catch((err: unknown) => {
          setPlan(null);
          setError(err instanceof Error ? err.message : "Preview failed");
        })
        .finally(() => setLoading(false));
    },
    [model, provider],
  );

  const dispatch = useCallback(
    (run: any) => {
      const context = buildRemediationContext(run);
      if (!context) {
        setError("Select a failed run before dispatching remediation.");
        return;
      }
      setLoading(true);
      setDispatchState({
        note:
          "Dispatch submitted for " +
          context.repository +
          " #" +
          context.run_id +
          ". Waiting for agent heartbeat.",
      });
      legacyFetch("/api/agent-remediation/dispatch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...context, provider }),
      })
        .then((response) => parseJsonOrThrow(response, "Dispatch failed"))
        .then((payload) => {
          const result = normalizeObject(payload);
          setDispatchState({
            note:
              "Dispatched " +
              (result.provider || provider) +
              " through " +
              (result.workflow || "remediation workflow") +
              ".",
          });
          setError(null);
          void refreshHistory();
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : "Dispatch failed";
          setDispatchState({ error: message });
          setError(message);
        })
        .finally(() => setLoading(false));
    },
    [provider, refreshHistory],
  );

  return (
    <RemediationTab
      config={config}
      workflows={workflows}
      runs={runs}
      loading={loading}
      error={error}
      selectedRunId={selectedRunId}
      setSelectedRunId={setSelectedRunId}
      provider={provider}
      setProvider={setProvider}
      model={model}
      setModel={setModel}
      plan={plan}
      dispatchState={dispatchState}
      onRefresh={() => refresh()}
      onSaveConfig={saveConfig}
      onPreview={preview}
      onDispatch={dispatch}
      history={history}
      principalName="dashboard"
    />
  );
}

export default RemediationPage;
