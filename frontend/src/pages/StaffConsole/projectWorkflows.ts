/** Shared Barb starter text for named fleet repositories (issue #2535). */
export function repositoryScopedProjectPrompt(project: string): string {
  return (
    `Triage ${project}: review open issues and pull requests in the ${project} repository. ` +
    "Summarize active work and recommend the highest-value test-first next step. " +
    "Gather evidence using the repository's registered workflows, but wait for my approval before dispatching any work."
  );
}

export const PROJECT_WORKFLOWS = [
  {
    project: "UpstreamDrift",
    summary: "Review open work and identify the next useful action.",
    prompt: repositoryScopedProjectPrompt("UpstreamDrift"),
  },
  {
    project: "AffineDrift",
    summary: "Check project health and find a clear next step.",
    prompt: repositoryScopedProjectPrompt("AffineDrift"),
  },
] as const;
