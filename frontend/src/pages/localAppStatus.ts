/**
 * localAppStatus.ts — pure status predicates for the Local Tools page,
 * extracted from the legacy `App.tsx` monolith (decomposition #836, pass 3).
 *
 * Kept in their own module (separate from `LocalApps.tsx`) so the page file
 * exports only components — satisfying the `react-refresh/only-export-components`
 * fast-refresh rule — while these stay independently unit-testable.
 */

/** A local app's git-drift summary (only the fields these predicates read). */
export interface LocalAppDriftLike {
  available?: boolean;
  behind?: number;
  ahead?: number;
}

/** A local app's health-probe summary (only the fields these predicates read). */
export interface LocalAppHealthLike {
  available?: boolean;
  ok?: boolean;
}

/** The minimal shape needed to classify a local app's status. */
export interface LocalAppStatusLike {
  drift?: LocalAppDriftLike;
  health?: LocalAppHealthLike;
}

/** True when the app is strictly behind its tracked ref (an update is waiting). */
export function localAppHasUpdateAvailable(a: LocalAppStatusLike): boolean {
  return !!a.drift && (a.drift.behind || 0) > 0 && a.drift.ahead === 0;
}

/** True when the app's health probe ran and reported a failure. */
export function localAppUnhealthy(a: LocalAppStatusLike): boolean {
  return !!a.health && !!a.health.available && a.health.ok === false;
}

/** True when the app either has a pending update or is unhealthy. */
export function localAppNeedsAttention(a: LocalAppStatusLike): boolean {
  return localAppHasUpdateAvailable(a) || localAppUnhealthy(a);
}
