/**
 * Paths and surfaces excluded from the interactive-hint coverage audit.
 *
 * Deprecated aliases slated for removal stay out of the ratchet until deleted.
 * Another lane owns the global provider dropdown — skip it to avoid merge churn.
 */
export const HINT_COVERAGE_SKIP_PATH_SUFFIXES: readonly string[] = [
  "/pages/FleetOrchestrationPage.tsx",
  "/pages/Deployment.tsx",
  "/pages/Diagnostics.tsx",
  "/pages/FeatureRequests.tsx",
  "/pages/FeatureRequestsPage.tsx",
  "/lib/openapi.json",
  "/lib/api-types.ts",
  /** Global provider control — active refactor in another lane. */
  "/shell/ActiveProviderControl.tsx",
  "/primitives/ProviderModelSelector.tsx",
];

export function shouldSkipHintCoverageFile(relativePath: string): boolean {
  const normalized = relativePath.replace(/\\/g, "/");
  if (normalized.includes("/__tests__/")) return true;
  if (/\.(test|spec)\.(t|j)sx?$/.test(normalized)) return true;
  if (normalized.includes("@deprecated")) return false; // source scan handles @deprecated file headers
  return HINT_COVERAGE_SKIP_PATH_SUFFIXES.some((suffix) => normalized.endsWith(suffix));
}
