import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { TestsTab, type CiResult, type TestRepo } from "./Tests";

interface TestReposPayload {
  repos?: TestRepo[];
}

interface CiResultsPayload {
  results?: CiResult[];
}

function normalizeTestReposPayload(payload: unknown): TestRepo[] {
  if (Array.isArray(payload)) return payload as TestRepo[];
  if (payload && typeof payload === "object") {
    const repos = (payload as TestReposPayload).repos;
    if (Array.isArray(repos)) return repos;
  }
  return [];
}

function normalizeCiResultsPayload(payload: unknown): CiResult[] {
  if (Array.isArray(payload)) return payload as CiResult[];
  if (payload && typeof payload === "object") {
    const results = (payload as CiResultsPayload).results;
    if (Array.isArray(results)) return results;
  }
  return [];
}

export function TestsPage(): React.ReactElement {
  const [testRepos, setTestRepos] = useState<TestRepo[]>([]);
  const [ciResults, setCiResults] = useState<CiResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    Promise.all([
      legacyFetch("/api/heavy-tests/repos", { signal }).then((r) => {
        if (!r.ok) throw new Error("heavy tests HTTP " + r.status);
        return r.json();
      }),
      legacyFetch("/api/tests/ci-results", { signal }).then((r) => {
        if (!r.ok) throw new Error("CI results HTTP " + r.status);
        return r.json();
      }),
    ])
      .then(([reposPayload, ciPayload]) => {
        setTestRepos(normalizeTestReposPayload(reposPayload));
        setCiResults(normalizeCiResultsPayload(ciPayload));
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err.message : "Failed to load test data",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <div>
      {error ? (
        <div
          className="section"
          role="alert"
          style={{ marginBottom: 12, color: "var(--accent-red)" }}
        >
          Failed to load test data: {error}
          <button
            className="btn"
            type="button"
            onClick={() => refresh()}
            style={{ marginLeft: 12 }}
          >
            Retry
          </button>
        </div>
      ) : null}
      <TestsTab testRepos={testRepos} loading={loading} ciResults={ciResults} />
    </div>
  );
}

export default TestsPage;
