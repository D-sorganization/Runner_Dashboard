import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { RemediationTab } from "./RemediationTab";

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

interface RemediationContext {
  repository: string;
  workflow_name: string;
  branch: string;
  run_id: unknown;
  failure_reason: string;
  protected_branch: boolean;
  attempts: unknown[];
}

function readJsonOrThrow(response: Response, fallback: string): Promise<unknown> {
  return response.json().then((data: unknown) => {
    if (!response.ok) {
      const detail =
        data && typeof data === "object" && "detail" in data
          ? String((data as { detail?: unknown }).detail)
          : fallback;
      throw new Error(detail);
    }
    return data;
  });
}

function runsFromPayload(payload: unknown): unknown[] {
  if (!payload || typeof payload !== "object") return [];
  const runs = (payload as { workflow_runs?: unknown }).workflow_runs;
  return Array.isArray(runs) ? runs : [];
}

function historyFromPayload(payload: unknown): unknown[] {
  if (!payload || typeof payload !== "object") return [];
  const history = (payload as { history?: unknown }).history;
  return Array.isArray(history) ? history : [];
}

function defaultProvider(config: unknown): string {
  if (!config || typeof config !== "object") return "jules_api";
  const policy = (config as { policy?: { default_provider?: unknown } }).policy;
  return typeof policy?.default_provider === "string"
    ? policy.default_provider
    : "jules_api";
}

function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === "string" ? value : "";
}

function buildRemediationContext(run: unknown): RemediationContext | null {
  if (!run || typeof run !== "object") return null;
  const record = run as Record<string, unknown>;
  const repository = record.repository;
  const repositoryRecord =
    repository && typeof repository === "object"
      ? (repository as Record<string, unknown>)
      : {};
  const branch = stringField(record, "head_branch");
  const repoName = stringField(repositoryRecord, "name");
  const workflowName =
    stringField(record, "name") || stringField(record, "workflow_name") || "CI Standard";
  return {
    repository: repoName,
    workflow_name: workflowName,
    branch,
    run_id: record.id,
    failure_reason: `${workflowName} failed for ${repoName} on ${branch}`,
    protected_branch: branch === "main" || branch === "master",
    attempts: [],
  };
}

export function RemediationPage(): React.ReactElement {
  const [config, setConfig] = useState<unknown>({});
  const [workflows, setWorkflows] = useState<unknown>({});
  const [runs, setRuns] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState("jules_api");
  const [model, setModel] = useState("");
  const [plan, setPlan] = useState<unknown>(null);
  const [dispatchState, setDispatchState] = useState<unknown>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [history, setHistory] = useState<unknown[]>([]);

  const refreshHistory = useCallback(() => {
    return legacyFetch("/api/agent-remediation/history")
      .then((r) => readJsonOrThrow(r, "history failed"))
      .then((payload) => setHistory(historyFromPayload(payload)))
      .catch(() => undefined);
  }, []);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    Promise.all([
      legacyFetch("/api/agent-remediation/config", { signal }).then((r) =>
        readJsonOrThrow(r, "config failed"),
      ),
      legacyFetch("/api/agent-remediation/workflows", { signal }).then((r) =>
        readJsonOrThrow(r, "workflows failed"),
      ),
      legacyFetch("/api/agent-remediation/history", { signal })
        .then((r) => readJsonOrThrow(r, "history failed"))
        .catch(() => ({ history: [] })),
      legacyFetch("/api/runs/enriched?per_page=50", { signal })
        .then((r) => readJsonOrThrow(r, "runs failed"))
        .catch(() => ({ workflow_runs: [] })),
    ])
      .then(([configPayload, workflowsPayload, historyPayload, runsPayload]) => {
        setConfig(configPayload || {});
        setWorkflows(workflowsPayload || {});
        setHistory(historyFromPayload(historyPayload));
        setRuns(runsFromPayload(runsPayload));
        setProvider(defaultProvider(configPayload));
        setError(null);
      })
      .catch((refreshError: unknown) => {
        if (refreshError instanceof DOMException && refreshError.name === "AbortError") {
          return;
        }
        setError("Failed to load remediation controls from the dashboard backend.");
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const saveConfig = useCallback((policy: unknown) => {
    setLoading(true);
    return legacyFetch("/api/agent-remediation/config", {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({ policy }),
    })
      .then((r) => readJsonOrThrow(r, "Save failed"))
      .then((payload) => {
        setConfig(payload || {});
        setProvider(defaultProvider(payload));
        setError(null);
        return payload;
      })
      .catch((saveError: unknown) => {
        const message = saveError instanceof Error ? saveError.message : "Save failed";
        setError(message);
        throw saveError;
      })
      .finally(() => setLoading(false));
  }, []);

  const preview = useCallback(
    (run: unknown) => {
      const payload = buildRemediationContext(run);
      if (!payload) {
        setError("Select a failed run before previewing remediation.");
        return;
      }
      setLoading(true);
      setDispatchState(null);
      legacyFetch("/api/agent-remediation/plan", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          ...payload,
          provider_override: provider,
          model_override: model || undefined,
        }),
      })
        .then((r) => readJsonOrThrow(r, "Preview failed"))
        .then((payloadData) => {
          setPlan(payloadData);
          setError(null);
        })
        .catch((previewError: unknown) => {
          setPlan(null);
          setError(previewError instanceof Error ? previewError.message : "Preview failed");
        })
        .finally(() => setLoading(false));
    },
    [model, provider],
  );

  const dispatch = useCallback(
    (run: unknown) => {
      const payload = buildRemediationContext(run);
      if (!payload) {
        setError("Select a failed run before dispatching remediation.");
        return;
      }
      setLoading(true);
      setDispatchState({
        note: `Dispatch submitted for ${payload.repository} #${payload.run_id}. Waiting for agent heartbeat.`,
      });
      legacyFetch("/api/agent-remediation/dispatch", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({ ...payload, provider }),
      })
        .then((r) => readJsonOrThrow(r, "Dispatch failed"))
        .then((payloadData) => {
          const dispatchPayload =
            payloadData && typeof payloadData === "object"
              ? (payloadData as Record<string, unknown>)
              : {};
          setDispatchState({
            note: `Dispatched ${stringField(dispatchPayload, "provider")} through ${stringField(
              dispatchPayload,
              "workflow",
            )}.`,
          });
          setError(null);
          refreshHistory();
        })
        .catch((dispatchError: unknown) => {
          const message =
            dispatchError instanceof Error ? dispatchError.message : "Dispatch failed";
          setDispatchState({ error: message });
          setError(message);
        })
        .finally(() => setLoading(false));
    },
    [provider, refreshHistory],
  );

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

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
    />
  );
}

export default RemediationPage;
