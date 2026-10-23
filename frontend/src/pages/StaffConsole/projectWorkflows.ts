export const PROJECT_WORKFLOWS = [
  {
    project: "UpstreamDrift",
    summary: "Review open work and identify the next useful action.",
    prompt:
      "Triage UpstreamDrift: review open issues and pull requests in the UpstreamDrift repository. Summarize active work and recommend the highest-value test-first next step. Gather evidence using the repository's registered workflows, but wait for my approval before dispatching any work.",
  },
  {
    project: "AffineDrift",
    summary: "Check project health and find a clear next step.",
    prompt:
      "Triage AffineDrift: review open issues and pull requests in the AffineDrift repository. Summarize active work and recommend the highest-value test-first next step. Gather evidence using the repository's registered workflows, but wait for my approval before dispatching any work.",
  },
] as const;
