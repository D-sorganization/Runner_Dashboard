import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import {
  WorkflowsTab,
  type Workflow,
  type WorkflowDispatch,
} from "./Workflows";

interface WorkflowsPayload {
  workflows?: Workflow[];
}

function normalizeWorkflowsPayload(payload: unknown): Workflow[] {
  if (Array.isArray(payload)) return payload as Workflow[];
  if (payload && typeof payload === "object") {
    const workflows = (payload as WorkflowsPayload).workflows;
    if (Array.isArray(workflows)) return workflows;
  }
  return [];
}

export function WorkflowsPage(): React.ReactElement {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    legacyFetch("/api/workflows/list", { signal })
      .then((r) => {
        if (!r.ok) throw new Error("workflows HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        setWorkflows(normalizeWorkflowsPayload(payload));
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err.message : "Failed to load workflows list.",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const dispatchWorkflow = useCallback((payload: WorkflowDispatch) => {
    return legacyFetch("/api/workflows/dispatch", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    }).then((r) =>
      r.json().then((data: unknown) => {
        if (!r.ok) {
          const detail =
            data && typeof data === "object" && "detail" in data
              ? String((data as { detail?: unknown }).detail)
              : "Dispatch failed";
          throw new Error(detail);
        }
        return data;
      }),
    );
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <WorkflowsTab
      workflows={workflows}
      loading={loading}
      error={error}
      onDispatch={dispatchWorkflow}
      onRefresh={() => refresh()}
    />
  );
}

export default WorkflowsPage;
