import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import {
  FleetOrchestrationTab,
  type FleetOrchestrationData,
  type OrchestrationDeployPayload,
  type OrchestrationDispatchPayload,
} from "./FleetOrchestration";

function normalizeFleetOrchestrationPayload(payload: unknown): FleetOrchestrationData {
  if (!payload || typeof payload !== "object") return {};
  return payload as FleetOrchestrationData;
}

function parseJsonOrThrow(response: Response, fallback: string): Promise<unknown> {
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

function normalizeDispatchResponse(payload: unknown): { audit_id?: string } {
  if (!payload || typeof payload !== "object") return {};
  const auditId = (payload as { audit_id?: unknown }).audit_id;
  return typeof auditId === "string" ? { audit_id: auditId } : {};
}

function normalizeDeployResponse(payload: unknown): { message?: string } {
  if (!payload || typeof payload !== "object") return {};
  const message = (payload as { message?: unknown }).message;
  return typeof message === "string" ? { message } : {};
}

export function FleetOrchestrationPage(): React.ReactElement {
  const [data, setData] = useState<FleetOrchestrationData>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    legacyFetch("/api/fleet/orchestration", { signal })
      .then((r) => parseJsonOrThrow(r, "fleet orchestration failed"))
      .then((payload) => {
        setData(normalizeFleetOrchestrationPayload(payload));
        setError(null);
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError("Failed to load fleet orchestration data.");
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const dispatchWorkflow = useCallback((payload: OrchestrationDispatchPayload) => {
    return legacyFetch("/api/fleet/orchestration/dispatch", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    })
      .then((r) => parseJsonOrThrow(r, "Dispatch failed"))
      .then(normalizeDispatchResponse);
  }, []);

  const deployAction = useCallback((payload: OrchestrationDeployPayload) => {
    return legacyFetch("/api/fleet/orchestration/deploy", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(payload),
    })
      .then((r) => parseJsonOrThrow(r, "Deploy failed"))
      .then(normalizeDeployResponse);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <FleetOrchestrationTab
      data={data}
      loading={loading}
      error={error}
      onRefresh={() => refresh()}
      onDispatch={dispatchWorkflow}
      onDeploy={deployAction}
    />
  );
}

export default FleetOrchestrationPage;
