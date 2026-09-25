/**
 * threadTypes.ts — Types and interfaces for the Staff Console Thread View & Composer.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350) / Umbrella #1354.
 */

export interface ThreadMessage {
  id: string;
  thread_id: string;
  author_kind: "user" | "role" | "system";
  author: string;
  body_md: string;
  created_at: string;
  delivery?: "pending" | "complete" | "failed";
  kind?: "text" | "error" | "action_proposal" | "handoff";
  meta?: Record<string, unknown>;
}

export type SendStatus = "idle" | "sending" | "sent" | "failed";

export interface SlashCommand {
  name: string;
  label: string;
  description: string;
  template?: string;
}

export const SLASH_COMMANDS: readonly SlashCommand[] = [
  {
    name: "/dispatch",
    label: "/dispatch",
    description: "Dispatch a work run with selected role",
    template: "/dispatch role: ",
  },
  {
    name: "/review",
    label: "/review",
    description: "Request an architectural or PR review",
    template: "/review pr: ",
  },
  {
    name: "/status",
    label: "/status",
    description: "Query current fleet, scheduler, and node status",
    template: "/status",
  },
  {
    name: "/hold",
    label: "/hold",
    description: "Check or set operational holds",
    template: "/hold ",
  },
  {
    name: "/brief",
    label: "/brief",
    description: "Request an executive status briefing from Barb",
    template: "/brief",
  },
] as const;

export interface MentionSuggestion {
  name: string;
  title: string;
  avatar?: string;
  group?: string;
}

export interface ThreadViewProps {
  threadId: string;
  roleName: string;
  roleTitle?: string;
  messages: ThreadMessage[];
  isLoading?: boolean;
  isStreaming?: boolean;
  isReconnecting?: boolean;
  onSendMessage: (body: string, idempotencyKey: string) => Promise<void> | void;
  onRetryMessage?: (messageId: string, idempotencyKey: string) => Promise<void> | void;
  onStopStreaming?: () => void;
  availableRoles?: MentionSuggestion[];
  className?: string;
}

export interface ComposerProps {
  threadId: string;
  onSend: (text: string, idempotencyKey: string) => Promise<void> | void;
  disabled?: boolean;
  isSending?: boolean;
  placeholder?: string;
  availableRoles?: MentionSuggestion[];
  className?: string;
}
