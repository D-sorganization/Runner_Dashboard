/**
 * useStalenessWarning — converts a TanStack Query query state into a
 * human-friendly staleness indicator (D3 / issue #722).
 *
 * Preconditions:
 *  - query.dataUpdatedAt: Unix timestamp (ms) of the last successful fetch, 0 if never.
 *  - query.errorUpdatedAt: Unix timestamp (ms) of the last error, 0 if none.
 *  - query.failureCount: number of consecutive failures (>= 0).
 *  - freshMs: positive number of milliseconds defining the "fresh" window.
 *
 * Postconditions:
 *  - state = "error"  when failureCount >= 2 (network is broken).
 *  - state = "stale"  when Date.now() - dataUpdatedAt >= freshMs and failureCount < 2.
 *  - state = "fresh"  when data was updated within the freshMs window.
 *  - lastSuccessAt is null when dataUpdatedAt === 0.
 */

export type StalenessState = 'fresh' | 'stale' | 'error';

export interface Staleness {
  state: StalenessState;
  lastSuccessAt: Date | null;
  failureCount: number;
  isFetching: boolean;
}

export interface StalenessQuery {
  dataUpdatedAt: number;
  errorUpdatedAt: number;
  failureCount: number;
  isFetching: boolean;
}

export function useStalenessWarning(
  query: StalenessQuery,
  freshMs = 60_000,
): Staleness {
  // Precondition guard (dev-only)
  if (process.env.NODE_ENV !== 'production') {
    console.assert(freshMs > 0, '[useStalenessWarning] freshMs must be positive');
  }

  const { dataUpdatedAt, failureCount, isFetching } = query;

  const lastSuccessAt = dataUpdatedAt > 0 ? new Date(dataUpdatedAt) : null;

  let state: StalenessState;

  if (failureCount >= 2) {
    state = 'error';
  } else if (dataUpdatedAt === 0 || Date.now() - dataUpdatedAt >= freshMs) {
    state = 'stale';
  } else {
    state = 'fresh';
  }

  return { state, lastSuccessAt, failureCount, isFetching };
}
