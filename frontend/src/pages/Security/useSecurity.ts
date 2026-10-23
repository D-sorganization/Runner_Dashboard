import { useCallback, useEffect, useState } from "react";
import {
  type ContractsOverview,
  type CoverageOverview,
  type Finding,
  type FindingDetail,
  type FindingFilters,
  type PostureOverview,
  SecurityApiError,
  fetchContracts,
  fetchCoverage,
  fetchFinding,
  fetchFindings,
  fetchPosture,
  noteRequired,
  triageFinding,
} from "./securityApi";

/** Load state for one section; sections fail independently. */
export type Section<T> =
  | { kind: "loading" }
  | { kind: "disabled" }
  | { kind: "forbidden"; message: string }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: T };

function toSection<T>(cause: unknown, fallback: string): Section<T> {
  if (cause instanceof SecurityApiError) {
    if (cause.code === "disabled") return { kind: "disabled" };
    if (cause.code === "forbidden") return { kind: "forbidden", message: cause.message };
    return { kind: "error", message: cause.message };
  }
  return { kind: "error", message: cause instanceof Error ? cause.message : fallback };
}

export interface TriageState {
  pending: boolean;
  error: string | null;
}

export function useSecurity() {
  const [contracts, setContracts] = useState<Section<ContractsOverview>>({ kind: "loading" });
  const [posture, setPosture] = useState<Section<PostureOverview>>({ kind: "loading" });
  const [coverage, setCoverage] = useState<Section<CoverageOverview>>({ kind: "loading" });
  const [findings, setFindings] = useState<Section<Finding[]>>({ kind: "loading" });
  const [filters, setFilters] = useState<FindingFilters>({});
  const [detail, setDetail] = useState<FindingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [triage, setTriage] = useState<TriageState>({ pending: false, error: null });

  const loadOverview = useCallback(async () => {
    setContracts({ kind: "loading" });
    setPosture({ kind: "loading" });
    setCoverage({ kind: "loading" });
    await Promise.all([
      fetchContracts().then(
        (data) => setContracts({ kind: "ready", data }),
        (cause: unknown) => setContracts(toSection(cause, "Unable to load contracts")),
      ),
      fetchPosture().then(
        (data) => setPosture({ kind: "ready", data }),
        (cause: unknown) => setPosture(toSection(cause, "Unable to load posture")),
      ),
      fetchCoverage().then(
        (data) => setCoverage({ kind: "ready", data }),
        (cause: unknown) => setCoverage(toSection(cause, "Unable to load scan coverage")),
      ),
    ]);
  }, []);

  const loadFindings = useCallback(async (current: FindingFilters) => {
    setFindings({ kind: "loading" });
    try {
      setFindings({ kind: "ready", data: await fetchFindings(current) });
    } catch (cause) {
      setFindings(toSection(cause, "Unable to load findings"));
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    void loadFindings(filters);
  }, [loadFindings, filters]);

  const selectFinding = useCallback(async (id: string) => {
    setDetailLoading(true);
    setDetailError(null);
    setTriage({ pending: false, error: null });
    try {
      setDetail(await fetchFinding(id));
    } catch (cause) {
      setDetail(null);
      setDetailError(cause instanceof Error ? cause.message : "Unable to load finding");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const closeFinding = useCallback(() => {
    setDetail(null);
    setDetailError(null);
    setTriage({ pending: false, error: null });
  }, []);

  /** Precondition mirrors the backend: suppressing states need a note. */
  const submitTriage = useCallback(
    async (id: string, state: string, note: string): Promise<boolean> => {
      if (noteRequired(state) && !note.trim()) {
        setTriage({ pending: false, error: "A note is required for this state." });
        return false;
      }
      setTriage({ pending: true, error: null });
      try {
        await triageFinding(id, state, note.trim());
        setDetail(await fetchFinding(id));
        setTriage({ pending: false, error: null });
        void loadFindings(filters);
        return true;
      } catch (cause) {
        setTriage({ pending: false, error: cause instanceof Error ? cause.message : "Triage failed" });
        return false;
      }
    },
    [filters, loadFindings],
  );

  return {
    contracts,
    posture,
    coverage,
    findings,
    filters,
    setFilters,
    refetch: () => {
      void loadOverview();
      void loadFindings(filters);
    },
    detail,
    detailLoading,
    detailError,
    selectFinding,
    closeFinding,
    triage,
    submitTriage,
  };
}
