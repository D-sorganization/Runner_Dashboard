/**
 * Fleet Command tab types (issue #1233, epic #1192).
 *
 * Mirrors `docs/priorities-api.md` (#1227) and `docs/coordination-api.md`
 * (#1229). The backend routes return `dict[str, Any]`, so the generated
 * `lib/api-types.ts` carries no response shapes for them; these local records
 * are the contract (same approach as `pages/Staff/staffApi.ts`).
 */

/** Every read may answer `{available: false, reason}` instead of failing. */
export interface Availability {
  available?: boolean;
  reason?: string;
  generated_at?: string;
}

// ── Priorities (/api/priorities) ─────────────────────────────────────────────

export interface ActivePriority {
  rank: number | string;
  item: string;
  project: string;
  scope?: string;
  assigned_to: string;
  tracking: string;
  acceptance?: string;
}

export interface DeferredItem {
  item: string;
  project: string;
  reason: string;
  reassess: string;
}

export interface BordaRow {
  rank: number | string;
  item: string;
  project: string;
  score: number | string;
  votes?: string;
}

export interface Consensus {
  metadata?: Record<string, string>;
  active: ActivePriority[];
  deferred: DeferredItem[];
  borda?: BordaRow[];
  disagreements: string[];
}

export interface BoardSnapshot extends Consensus {
  date: string;
}

export interface Directive {
  id?: string;
  text: string;
  repo: string;
  priority: number;
  expires?: string | null;
  set_by?: string | null;
  set_on?: string | null;
}

export interface Portfolio {
  name: string;
  description?: string;
  wip_limit?: number | null;
  review_cadence?: string;
  unattended_agents?: string[];
  repos?: string[];
}

export interface PrioritiesResponse extends Availability {
  board: BoardSnapshot | null;
  directives: Directive[];
  portfolios?: Portfolio[];
  portfolios_reason?: string;
}

export interface MeetingSummary {
  date: string;
  files: string[];
  has_consensus: boolean;
}

export interface MeetingsResponse extends Availability {
  meetings: MeetingSummary[];
}

export interface MeetingDetail extends Availability {
  date: string;
  files?: string[];
  consensus: Consensus | null;
  consensus_markdown?: string | null;
  packet?: string | null;
  instructions?: string | null;
  pathway_log_entry?: string | null;
}

export interface DirectivesResponse {
  directives: Directive[];
  /** Fingerprint of the stored list; send it back with the PUT (409 when stale). */
  version?: string;
}

// ── Coordination (/api/coordination) ─────────────────────────────────────────

export interface BoardSession {
  session: string;
  agent: string;
  repo: string;
  issue?: number | null;
  branch?: string;
  paths?: string[];
  goals?: Record<string, string>;
  expires?: string;
  at?: string;
  source?: "board" | string;
}

export interface StaffRunSummary {
  id: string;
  role: string;
  provider: string;
  machine: string;
  repo: string;
  target?: string;
  status: string;
  started_at?: string | null;
  source?: "staff" | string;
}

export interface BoardMessage {
  id: string;
  session: string;
  repo: string;
  recipient: string;
  text: string;
  at?: string;
}

/** RM `agent_messages.conflicts`: advisory path/goal overlap with another session. */
export interface BoardConflict {
  session: string;
  agent: string;
  issue?: number | null;
  branch?: string;
  paths: string[];
  goals: string[];
}

export interface SessionsResponse extends Availability {
  complete?: boolean;
  sessions: BoardSession[];
  staff_runs: StaffRunSummary[];
  messages?: BoardMessage[];
  conflicts?: BoardConflict[];
  warnings?: string[];
}

export interface InboxResponse extends Availability {
  complete?: boolean;
  messages: BoardMessage[];
  conflicts: BoardConflict[];
  warnings?: string[];
}

export interface ClaimStatus extends Availability {
  held: boolean;
  agent?: string | null;
  expires_at?: string | null;
}

/** `POST /api/coordination/presence` — advertise a session on the board (RM drops mail from unknown senders). */
export interface PresenceBody {
  agent: string;
  session: string;
  repo: string;
  issue: number;
  branch: string;
  ttl_hours: number;
}

export interface MessageBody {
  session: string;
  repo: string;
  to: string;
  text: string;
}

export interface ClaimBody {
  repo: string;
  issue: number;
  session: string;
  agent?: string;
  intent?: string;
}

export interface ClaimReleaseBody {
  repo: string;
  issue: number;
  session: string;
  agent?: string;
  reason?: string;
}

export interface WriteReceipt {
  ok?: boolean;
  claimed?: boolean;
  generated_at?: string;
}

// ── Proposals (/api/proposals) ───────────────────────────────────────────────

export interface ProposalItem {
  number: number;
  title: string;
  target_repos: string[];
  problem: string;
  evidence: string;
  options_considered: string;
  lean: string;
  estimated_cost: string;
  urgency: string;
  source: string;
  code_request_url?: string | null;
  state: "open" | "decided" | "closed";
  decision?: "accepted" | "declined" | "deferred" | "decided" | null;
  decision_labels: string[];
  meeting_date?: string | null;
  consensus_url?: string | null;
  html_url?: string;
  created_at: string;
  updated_at: string;
  closed_at?: string | null;
  comments_count?: number;
}

export interface ProposalComment {
  id: number;
  user: { login: string; avatar_url?: string };
  body: string;
  created_at: string;
  is_secretary: boolean;
}

export interface ProposalDetail extends ProposalItem {
  comments: ProposalComment[];
}

export interface ProposalsResponse extends Availability {
  proposals: ProposalItem[];
  total: number;
}

/** Mirrors the board-proposal issue form's "Estimated Effort" dropdown exactly. */
export type ProposalEstimatedEffort = "Low" | "Medium" | "High";
/** Mirrors the board-proposal issue form's "Urgency" dropdown exactly. */
export type ProposalUrgency = "Routine" | "Urgent" | "Emergency";

export interface CreateProposalPayload {
  title: string;
  target_repos: string[] | string;
  problem: string;
  evidence: string;
  options_considered: string | string[];
  lean: string;
  estimated_cost: ProposalEstimatedEffort;
  urgency: ProposalUrgency;
  source?: string;
  code_request_url?: string;
  confirm_not_duplicate?: boolean;
}

export interface DuplicateCandidate {
  number: number;
  title: string;
  url: string;
}
