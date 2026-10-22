import { useCallback, useEffect, useState } from "react";
import {
  type DeliveryMetricsSummary,
  type PullDrillDown,
  fetchMetricsSummary,
  fetchPullDrillDown,
} from "./deliveryMetricsApi";

export type DeliveryMetricsState =
  | { kind: "loading" }
  | { kind: "disabled" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: DeliveryMetricsSummary; repoFilter: string | null };

export function useDeliveryMetrics() {
  const [state, setState] = useState<DeliveryMetricsState>({ kind: "loading" });
  const [repoFilter, setRepoFilter] = useState<string | null>(null);
  const [pullDetail, setPullDetail] = useState<PullDrillDown | null>(null);
  const [pullLoading, setPullLoading] = useState(false);
  const [pullError, setPullError] = useState<string | null>(null);

  const load = useCallback(async (repo: string | null) => {
    setState({ kind: "loading" });
    setPullDetail(null);
    setPullError(null);
    try {
      const data = await fetchMetricsSummary(repo);
      setState({ kind: "ready", data, repoFilter: repo });
    } catch (cause) {
      const code = (cause as Error & { code?: string }).code;
      if (code === "disabled") {
        setState({ kind: "disabled" });
        return;
      }
      const message = cause instanceof Error ? cause.message : "Unable to load delivery metrics";
      setState({ kind: "error", message });
    }
  }, []);

  useEffect(() => {
    void load(repoFilter);
  }, [load, repoFilter]);

  const selectPull = useCallback(async (repoFull: string, number: number) => {
    setPullLoading(true);
    setPullError(null);
    try {
      setPullDetail(await fetchPullDrillDown(repoFull, number));
    } catch (cause) {
      setPullDetail(null);
      setPullError(cause instanceof Error ? cause.message : "Unable to load pull detail");
    } finally {
      setPullLoading(false);
    }
  }, []);

  const clearPull = useCallback(() => {
    setPullDetail(null);
    setPullError(null);
  }, []);

  return {
    state,
    repoFilter,
    setRepoFilter,
    refetch: () => void load(repoFilter),
    pullDetail,
    pullLoading,
    pullError,
    selectPull,
    clearPull,
  };
}
