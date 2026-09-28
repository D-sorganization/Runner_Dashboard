import React, { useCallback, useEffect, useRef, useState } from "react";
import { legacyFetch } from "../lib/api";
import {
  WorkflowsTab,
  type Workflow,
  type WorkflowDispatch,
} from "./Workflows";

interface WorkflowsPayload {
  workflows?: Workflow[];
  status?: string;
}

/** The hub answers `status: "warming"` while its first GitHub sweep runs. */
function isWarming(payload: unknown): boolean {
  return (
    Boolean(payload) &&
    typeof payload === "object" &&
    (payload as WorkflowsPayload).status === "warming"
  );
}

const WARMING_MESSAGE =
  "Gathering workflows from GitHub. This first load can take a minute; retrying automatically…";

function normalizeWorkflowsPayload(payload: unknown): Workflow[] {
  if (Array.isArray(payload)) return payload as Workflow[];
  if (payload && typeof payload === "object") {
    const workflows = (payload as WorkflowsPayload).workflows;
    if (Array.isArray(workflows)) return workflows;
  }
  return [];
}

export function WorkflowsPage({
  warmingRetryMs = 10_000,
}: {
  /** Delay before re-asking while the hub's catalogue is warming. */
  warmingRetryMs?: number;
} = {}): React.ReactElement {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [warming, setWarming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const refresh = useCallback((signal?: AbortSignal) => {
    clearTimeout(retryTimer.current);
    setLoading(true);
    legacyFetch("/api/workflows/list", { signal })
      .then((r) => {
        if (!r.ok) throw new Error("workflows HTTP " + r.status);
        return r.json();
      })
      .then((payload) => {
        const stillWarming = isWarming(payload);
        setWarming(stillWarming);
        if (stillWarming && !signal?.aborted) {
          retryTimer.current = setTimeout(() => refresh(signal), warmingRetryMs);
        }
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
  }, [warmingRetryMs]);

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
    return () => {
      controller.abort();
      clearTimeout(retryTimer.current);
    };
  }, [refresh]);

  return (
    <WorkflowsTab
      workflows={workflows}
      loading={loading || warming}
      loadingMessage={warming ? WARMING_MESSAGE : undefined}
      error={error}
      onDispatch={dispatchWorkflow}
      onRefresh={() => refresh()}
    />
  );
}

export default WorkflowsPage;
