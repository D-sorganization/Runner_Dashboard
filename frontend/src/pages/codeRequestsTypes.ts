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

/** The standards text is injected server-side, once, for both dispatch paths (#1501). */
export function buildCodeRequest(payload: CodeDispatchPayload): WorkRequest {
  return {
    kind: "code_request.dispatch",
    target: {
      repo: payload.repository,
      ref: payload.branch,
    },
    provider: payload.provider || null,
    model: payload.model || null,
    profile_id: payload.profile_id || null,
    prompt: payload.prompt,
    standards: payload.standards,
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
