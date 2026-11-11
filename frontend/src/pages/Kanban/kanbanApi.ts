import { useCallback, useEffect, useState } from "react";
import { ApiClientError, apiRequest } from "../../lib/api";
import demoBoard from "./fixtures/demoBoard.json";
import type { KanbanGithubBoardResponse } from "./types";

const GITHUB_ORG = "D-sorganization";
const DEFAULT_REPO = "Runner_Dashboard";

function qs(params: Record<string, string | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export async function fetchKanbanBoard(
  owner: string,
  repo: string,
  signal?: AbortSignal,
): Promise<{ data: KanbanGithubBoardResponse; usedFixture: boolean }> {
  const path = `/api/kanban/github${qs({ owner, repo })}`;
  try {
    const data = await apiRequest<KanbanGithubBoardResponse>(path, { signal });
    return { data, usedFixture: Boolean(data.fixture) };
  } catch (err) {
    if (err instanceof ApiClientError && err.status === 404) {
      const fixture = demoBoard as KanbanGithubBoardResponse;
      return {
        data: {
          ...fixture,
          owner,
          repo,
          fixture: true,
        },
        usedFixture: true,
      };
    }
    throw err;
  }
}

export interface KanbanBoardResource {
  data: KanbanGithubBoardResponse | null;
  loading: boolean;
  error: string | null;
  usedFixture: boolean;
  reload: () => void;
}

export function useKanbanBoard(repo: string): KanbanBoardResource {
  const [data, setData] = useState<KanbanGithubBoardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [usedFixture, setUsedFixture] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    fetchKanbanBoard(GITHUB_ORG, repo, controller.signal)
      .then(({ data: payload, usedFixture: fixture }) => {
        if (controller.signal.aborted) return;
        setData(payload);
        setUsedFixture(fixture);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setData(null);
        setLoading(false);
      });
    return () => controller.abort();
  }, [repo, tick]);

  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, loading, error, usedFixture, reload };
}

export { DEFAULT_REPO, GITHUB_ORG };
