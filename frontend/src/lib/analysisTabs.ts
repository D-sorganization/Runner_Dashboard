/**
 * analysisTabs.ts — the canonical set of Analysis sub-tab keys, extracted from
 * the legacy `App.tsx` monolith (decomposition #836, pass 12).
 *
 * Shared by the legacy App shell (top-level tab routing / active-state) and the
 * `AnalysisTab` orchestrator, so both agree on which keys belong to the
 * Analysis surface without duplicating the list.
 */

/** The tab keys that route into the Analysis surface. */
export const ANALYSIS_TAB_KEYS = [
  "analysis",
  "stats",
  "performance",
  "reports",
  "history",
] as const;

/** True when `key` is one of the Analysis sub-tab keys. */
export function isAnalysisTabKey(key: unknown): boolean {
  return ANALYSIS_TAB_KEYS.indexOf(key as (typeof ANALYSIS_TAB_KEYS)[number]) >= 0;
}
