/**
 * remediationDispatch.ts — pure, React-free helpers shared by the Remediation
 * "PRs" and "Issues" sub-tabs (`RemediationPRs.tsx` / `RemediationIssues.tsx`),
 * extracted 1:1 from the legacy `App.tsx` monolith (decomposition #836, pass 8).
 *
 * Kept in a `.ts` file (no component exports) so the component files satisfy
 * `react-refresh/only-export-components` without an eslint override — the same
 * split already used by `decompSort.ts`.
 */
import type React from "react";

type CSSProps = React.CSSProperties;

// ── PR helpers ────────────────────────────────────────────────────────────────

/** A label may arrive as a bare string or a `{ name }` object. */
export type PRLabel = string | { name?: string };

/** A pull-request row. The backend payload is loosely shaped, so every field is
 *  optional and several have historical aliases that the legacy code coalesced. */
export interface PullRequest {
  id?: number | string;
  number?: number;
  pr_number?: number;
  repo?: string;
  repository?: string;
  full_name?: string;
  repo_url?: string;
  repository_url?: string;
  html_url?: string;
  url?: string;
  title?: string;
  author?: string;
  user?: { login?: string };
  login?: string;
  draft?: boolean;
  labels?: PRLabel[];
  agent_claim?: string;
  age_hours?: number;
  created_at?: string;
}

/** Stable row id, matching the legacy `String(number || pr_number || id)`. */
export function prRowId(pr: PullRequest): string {
  return String(pr.number ?? pr.pr_number ?? pr.id);
}

/** Repo/author/draft filter predicate, 1:1 with the legacy inline filter. */
export function prMatchesFilters(
  pr: PullRequest,
  repoFilter: string,
  authorFilter: string,
  showDrafts: boolean,
): boolean {
  if (!showDrafts && pr.draft) return false;
  if (repoFilter) {
    const repo = (pr.repo || pr.repository || pr.full_name || "").toLowerCase();
    if (!repo.includes(repoFilter.toLowerCase())) return false;
  }
  if (authorFilter) {
    const author = (
      pr.author ||
      (pr.user && pr.user.login) ||
      pr.login ||
      ""
    ).toLowerCase();
    if (!author.includes(authorFilter.toLowerCase())) return false;
  }
  return true;
}

/** Age in hours, preferring the precomputed `age_hours`, then `created_at`. */
export function prAgeHours(pr: PullRequest): number | null {
  if (pr.age_hours != null) return pr.age_hours;
  if (pr.created_at) {
    return (Date.now() - new Date(pr.created_at).getTime()) / 3600000;
  }
  return null;
}

/** Human age label: "-" when unknown, "{n}h" under 48h, otherwise "{n}d". */
export function prAgeLabel(pr: PullRequest): string {
  const hours = prAgeHours(pr);
  if (hours == null) return "-";
  if (hours < 48) return hours.toFixed(0) + "h";
  return (hours / 24).toFixed(0) + "d";
}

// ── Issue helpers ─────────────────────────────────────────────────────────────

export interface IssueTaxonomy {
  type?: string;
  issue_type?: string;
  complexity?: string;
  judgement?: string;
  effort?: string;
  quick_win?: boolean;
}

export interface IssueRecord {
  repo?: string;
  repository?: string;
  number?: number;
  title?: string;
  url?: string;
  html_url?: string;
  pickable?: boolean;
  pickable_blocked_by?: string[];
  taxonomy?: IssueTaxonomy;
  sources?: string[];
  linear?: { id?: string; identifier?: string; url?: string };
}

/** Stable selection key for an issue, matching the legacy `issueKey`. */
export function issueKey(issue: IssueRecord): string {
  return [
    issue.repo || issue.repository || "",
    issue.number != null
      ? String(issue.number)
      : (issue.linear && issue.linear.id) || issue.url || issue.title || "linear",
  ].join(":");
}

export function getTypeStyle(type?: string): CSSProps {
  const map: Record<string, CSSProps> = {
    epic: { background: "var(--badge-neutral-bg)", color: "var(--badge-neutral-fg)" },
    task: { background: "var(--badge-info-bg)", color: "var(--badge-info-fg)" },
    bug: { background: "var(--badge-danger-bg)", color: "var(--badge-danger-fg)" },
    security: {
      background: "var(--badge-danger-bg)",
      color: "var(--badge-danger-fg)",
    },
    research: { background: "var(--badge-purple-bg)", color: "var(--accent-purple)" },
    docs: { background: "var(--badge-info-bg)", color: "var(--accent-blue)" },
    chore: {
      background: "var(--badge-neutral-bg)",
      color: "var(--badge-neutral-fg)",
    },
  };
  return (
    (type && map[type]) || {
      background: "var(--badge-neutral-bg)",
      color: "var(--badge-neutral-fg)",
    }
  );
}

export function getComplexityStyle(complexity?: string): CSSProps {
  const map: Record<string, CSSProps> = {
    trivial: {
      background: "var(--badge-success-bg)",
      color: "var(--badge-success-fg)",
    },
    routine: { background: "var(--badge-info-bg)", color: "var(--badge-info-fg)" },
    complex: {
      background: "var(--badge-warning-bg)",
      color: "var(--badge-warning-fg)",
    },
    deep: { background: "var(--badge-danger-bg)", color: "var(--badge-danger-fg)" },
    research: { background: "var(--badge-purple-bg)", color: "var(--accent-purple)" },
  };
  return (
    (complexity && map[complexity]) || {
      background: "var(--badge-neutral-bg)",
      color: "var(--badge-neutral-fg)",
    }
  );
}

export function getJudgementStyle(judgement?: string): CSSProps {
  if (judgement === "design" || judgement === "contested") {
    return { background: "var(--badge-danger-bg)", color: "var(--badge-danger-fg)" };
  }
  const map: Record<string, CSSProps> = {
    objective: {
      background: "var(--badge-success-bg)",
      color: "var(--badge-success-fg)",
    },
    preference: {
      background: "var(--badge-warning-bg)",
      color: "var(--badge-warning-fg)",
    },
  };
  return (
    (judgement && map[judgement]) || {
      background: "var(--badge-neutral-bg)",
      color: "var(--badge-neutral-fg)",
    }
  );
}

export function pillStyle(style: CSSProps): CSSProps {
  return {
    display: "inline-block",
    padding: "1px 7px",
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 600,
    whiteSpace: "nowrap",
    ...style,
  };
}

/** Source/repo/complexity/judgement/pickable filter, 1:1 with legacy. */
export function issueMatchesFilters(
  issue: IssueRecord,
  repoFilter: string,
  complexFilter: string,
  judgeFilter: string,
  pickableOnly: boolean,
): boolean {
  const taxonomy = issue.taxonomy || {};
  const repo = issue.repo || issue.repository || "";
  if (repoFilter && repo !== repoFilter) return false;
  if (complexFilter && taxonomy.complexity !== complexFilter) return false;
  if (judgeFilter && taxonomy.judgement !== judgeFilter) return false;
  if (pickableOnly && !issue.pickable) return false;
  return true;
}
