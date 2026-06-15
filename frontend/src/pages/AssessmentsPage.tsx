import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import {
  AssessmentsTab,
  type AssessmentDispatch,
  type AssessmentRepo,
  type AssessmentScore,
} from "./Assessments";

interface ReposPayload {
  repos?: (AssessmentRepo | string)[];
}

interface ScoresPayload {
  scores?: AssessmentScore[];
}

function normalizeReposPayload(payload: unknown): (AssessmentRepo | string)[] {
  if (Array.isArray(payload)) return payload as (AssessmentRepo | string)[];
  if (payload && typeof payload === "object") {
    const repos = (payload as ReposPayload).repos;
    if (Array.isArray(repos)) return repos;
  }
  return [];
}

function normalizeScoresPayload(payload: unknown): AssessmentScore[] {
  if (Array.isArray(payload)) return payload as AssessmentScore[];
  if (payload && typeof payload === "object") {
    const scores = (payload as ScoresPayload).scores;
    if (Array.isArray(scores)) return scores;
  }
  return [];
}

export function AssessmentsPage(): React.ReactElement {
  const [repos, setRepos] = useState<(AssessmentRepo | string)[]>([]);
  const [scores, setScores] = useState<AssessmentScore[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    Promise.all([
      legacyFetch("/api/repos", { signal }).then((r) => {
        if (!r.ok) throw new Error("repos HTTP " + r.status);
        return r.json();
      }),
      legacyFetch("/api/assessments/scores", { signal }).then((r) => {
        if (!r.ok) throw new Error("assessment scores HTTP " + r.status);
        return r.json();
      }),
    ])
      .then(([reposPayload, scoresPayload]) => {
        setRepos(normalizeReposPayload(reposPayload));
        setScores(normalizeScoresPayload(scoresPayload));
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load assessment scores",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  const dispatchAssessment = useCallback((payload: AssessmentDispatch) => {
    return legacyFetch("/api/assessments/dispatch", {
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
              : "dispatch failed";
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
    <AssessmentsTab
      repos={repos}
      scores={scores}
      loading={loading}
      error={error}
      onDispatch={dispatchAssessment}
      onRefresh={() => refresh()}
    />
  );
}

export default AssessmentsPage;
