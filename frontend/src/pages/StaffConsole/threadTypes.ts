/**
 * threadTypes.ts — Types and interfaces for the Staff Console Thread & Composer.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350) / Umbrella #1354.
 */

import type { ProposalApproveHandler, ProposalDenyHandler, RunCancelHandler } from "./cards/cardTypes";

export interface ThreadMessage {
  id: string;
  thread_id: string;
  author: string;
  author_kind: "user" | "bot" | "staff" | "system";
  kind: string; // "text" | "card" | "proposal" | "run" | "error" | "ack"
  body_md: string;
  delivery: "pending" | "complete" | "failed";
  meta?: Record<string, unknown>;
  idempotency_key?: string;
  created_at?: string;
  seq?: number;
  streaming?: boolean;
  error?: string | null;
  failure_class?: string | null;
  remediation?: string | null;
}

export interface ThreadInfo {
  id: string;
  title: string;
  kind: string; // "direct" | "auto" | "work"
  participants: string[];
  status: string; // "active" | "archived"
  created_by?: string;
  created_at?: string;
  updated_at?: string;
  last_message_at?: string;
  unread_count?: number;
  /** Group threads carry `group`, `coordinator` and `seats` (SC-B9). */
  meta?: Record<string, unknown>;
}

export interface SlashCommand {
  name: string;
  label: string;
  description: string;
  syntax: string;
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {
    name: "dispatch",
    label: "/dispatch",
    description: "Create formal staff work run",
    syntax: "/dispatch <role> <prompt>",
  },
  {
    name: "review",
    label: "/review",
    description: "Hand off pull request review to Fleet Critic",
    syntax: "/review <pr>",
  },
  {
    name: "status",
    label: "/status",
    description: "Request instantaneous fleet & budget status brief",
    syntax: "/status",
  },
  {
    name: "hold",
    label: "/hold",
    description: "Place temporary hold on automated actions",
    syntax: "/hold <rule>",
  },
  {
    name: "brief",
    label: "/brief",
    description: "Trigger morning/evening one-pager from Barb",
    syntax: "/brief",
  },
] as const;

export interface SendMessagePayload {
  body: string;
  idempotencyKey: string;
  role?: string;
  meta?: Record<string, unknown>;
}

export interface ComposerProps {
  threadId: string;
  roles?: import("./types").StaffRoleItem[];
  selectedRole?: string;
  onSendMessage: (payload: SendMessagePayload) => Promise<{ ok: boolean; [key: string]: unknown }>;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  /** Focus the textarea when the user switches to another thread (SC-D9). */
  focusOnThreadChange?: boolean;
}

export interface ThreadProps {
  thread: ThreadInfo;
  messages: ThreadMessage[];
  unreadSeqThreshold?: number;
  isReconnecting?: boolean;
  onStopStreaming?: (messageId: string) => void;
  onRetryMessage?: (message: ThreadMessage) => void;
  onSendMessage?: (payload: SendMessagePayload) => Promise<{ ok: boolean; [key: string]: unknown }>;
  roles?: import("./types").StaffRoleItem[];
  className?: string;
  onApproveProposal?: ProposalApproveHandler;
  onDenyProposal?: ProposalDenyHandler;
  onCancelRun?: RunCancelHandler;
  onAnswerRun?: (threadId: string, runId: string, answer: string) => Promise<boolean>;
  onRerouteHandoff?: (targetRole: string) => void;
}
