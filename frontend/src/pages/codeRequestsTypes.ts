/**
 * codeRequestsTypes.ts — Types and helpers for Code Requests (CR-1, #1281).
 */

import type { WorkRequest } from "./Staff/staffApi";

/** A repo entry may be a bare name string or an object carrying a `name`. */
export type CodeRepo = string | { name?: string };
export type FeatureRepo = CodeRepo;

export interface CodeRequestRecord {
  id?: string;
  repository?: string;
  branch?: string;
  prompt?: string;
  provider?: string;
  profile_id?: string;
  profile_snapshot?: Record<string, unknown>;
  standards?: string[];
  status?: string;
  created_at?: string;
  dispatched_at?: string;
  votes?: number;
  vote_count?: number;
  error?: string;
}
export type FeatureRequestRecord = CodeRequestRecord;

/** Whether the backend's dispatch workflow exists (#1280). */
export interface DispatchTargetStatus {
  workflow?: string;
  available?: boolean | null;
  detail?: string;
}

export interface PromptTemplate {
  name: string;
  prompt: string;
}

export interface PromptNotes {
  notes: string;
  enabled: boolean;
}

export interface CodeDispatchPayload {
  repository: string;
  branch: string;
  provider: string;
  prompt: string;
  standards: string[];
  profile_id?: string;
  model?: string;
}
export type FeatureDispatchPayload = CodeDispatchPayload;


export interface CodeRequestsProps {
  repos?: CodeRepo[];
  requests?: CodeRequestRecord[];
  dispatchTarget?: DispatchTargetStatus;
  templates?: PromptTemplate[];
  standards?: unknown;
  loading?: boolean;
  promptNotes?: PromptNotes;
  onDispatch?: (payload: CodeDispatchPayload) => Promise<unknown>;
  onSaveTemplate: (template: PromptTemplate) => Promise<unknown>;
  onSavePromptNotes: (notes: PromptNotes) => Promise<unknown>;
  onRefresh: () => void;
}
export type FeatureRequestsProps = CodeRequestsProps;

export const ALL_STANDARDS = ["tdd", "dbc", "dry", "lod", "security", "docs"];

export const STANDARDS_DESCRIPTIONS: Record<string, string> = {
  tdd: "Follow Test-Driven Development (TDD): write a failing test first, run it to verify failure, make it pass, then refactor.",
  dbc: "Follow Design by Contract (DbC): validate preconditions at public boundaries, assert invariants, and document postconditions.",
  dry: "Follow Don't Repeat Yourself (DRY): reuse existing helpers and libraries; do not copy-paste code blocks.",
  lod: "Follow Law of Demeter (LoD): talk only to immediate collaborators; do not chain calls through deep object graphs.",
  security: "Enforce strict security: sanitize all user input, prevent SQL/shell injection, never hardcode credentials.",
  docs: "Keep documentation in sync: update relevant markdown docs, docstrings, and architectural diagrams.",
};

export function buildCodeRequest(payload: CodeDispatchPayload): WorkRequest {
  let prompt = payload.prompt;
  if (payload.standards && payload.standards.length > 0) {
    const injected = payload.standards
      .map((s) => {
        const desc = STANDARDS_DESCRIPTIONS[s.toLowerCase()] || s;
        return `[${s.toUpperCase()}] ${desc}`;
      })
      .join("\n\n");
    prompt = `${prompt}\n\n## Engineering Standards\n${injected}`;
  }
  return {
    kind: "code_request.dispatch",
    target: {
      repo: payload.repository,
      ref: payload.branch,
    },
    provider: payload.provider || null,
    model: payload.model || null,
    profile_id: payload.profile_id || null,
    prompt,
    machine: "local",
    dry_run: false,
  };
}

export type DispatchStatus = "dispatching" | "ok" | "error" | null;
export type SaveStatus = "saving" | "ok" | "error" | null;

export function repoName(r: CodeRepo): string {
  return typeof r === "string" ? r : r.name || "";
}

export function requestDate(r: CodeRequestRecord): string {
  return r.created_at || r.dispatched_at
    ? String(r.created_at || r.dispatched_at).slice(0, 10)
    : "";
}

export function requestStatus(r: CodeRequestRecord): string {
  return r.status || "dispatched";
}

export function requestVoteCount(r: CodeRequestRecord): number {
  return r.votes != null ? r.votes : r.vote_count != null ? r.vote_count : 0;
}
