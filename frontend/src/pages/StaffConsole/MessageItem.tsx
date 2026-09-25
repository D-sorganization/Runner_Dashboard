/**
 * MessageItem.tsx — Renders individual thread messages: user bubbles,
 * staff markdown with code copying and link previews, streaming deltas with stop button,
 * and structured cards (Action, Run, Handoff, Review, Error) (SC-D5, Issue #1319).
 */
import React from "react";
import type { ThreadMessage } from "./threadTypes";
import { ThreadMarkdown } from "./threadMarkdown";
import { formatMessageTime } from "./threadUtils";
import { ActionCard, RunCard, HandoffCard, ReviewCard, ErrorCard } from "./cards";
import type {
  ActionProposalData,
  RunCardData,
  HandoffCardData,
  ReviewCardData,
  ErrorCardData,
  RiskLevel,
  ProposalState,
  RunStatus,
  ReviewVerdict,
} from "./cards";

export interface MessageItemProps {
  message: ThreadMessage;
  isStreaming?: boolean;
  onStopStreaming?: (messageId: string) => void;
  onRetry?: (message: ThreadMessage) => void;
  onApproveProposal?: (proposalId: string, params: Record<string, unknown>) => Promise<void> | void;
  onDenyProposal?: (proposalId: string, reason?: string) => Promise<void> | void;
  onCancelRun?: (runId: string) => Promise<void> | void;
  onRedirectHandoff?: (targetRole: string) => Promise<void> | void;
}

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isStreaming = false,
  onStopStreaming,
  onRetry,
  onApproveProposal,
  onDenyProposal,
  onCancelRun,
  onRedirectHandoff,
}) => {
  const isUser = message.author_kind === "user" || message.author === "user";
  const isFailed = message.delivery === "failed" || message.kind === "error";
  const isActivelyStreaming = isStreaming || message.streaming;
  const timeStr = formatMessageTime(message.created_at);

  const renderCardContent = () => {
    if (isFailed) {
      const errorData: ErrorCardData = {
        failure_class: message.failure_class || (message.meta?.failure_class as string) || null,
        message: message.body_md || (message.meta?.message as string) || message.error || "",
        remediation: message.remediation || (message.meta?.remediation as string) || null,
        command: (message.meta?.command as string) || null,
        node: (message.meta?.node as string) || null,
        retryable: typeof message.meta?.retryable === "boolean" ? message.meta.retryable : Boolean(onRetry),
      };
      return <ErrorCard error={errorData} onRetry={onRetry ? () => onRetry(message) : undefined} />;
    }

    if (message.kind === "action_proposal" || message.kind === "proposal" || message.kind === "action_result") {
      const proposalData: ActionProposalData = {
        id: (message.meta?.proposal_id as string) || (message.meta?.id as string) || message.id,
        action: (message.meta?.action as string) || message.body_md || "staff.action",
        target: (message.meta?.target as string) || undefined,
        risk: (message.meta?.risk as RiskLevel) || "medium",
        state: (message.meta?.state as ProposalState) || "proposed",
        params: (message.meta?.params as Record<string, unknown>) || {},
        decided_by: (message.meta?.decided_by as string) || null,
        decided_at: (message.meta?.decided_at as string) || null,
        reason: (message.meta?.reason as string) || null,
        expires_at: (message.meta?.expires_at as string) || null,
        is_expired: Boolean(message.meta?.is_expired),
      };
      return (
        <ActionCard
          proposal={proposalData}
          onApprove={onApproveProposal}
          onDeny={onDenyProposal}
        />
      );
    }

    if (message.kind === "run_card" || message.kind === "run") {
      const runData: RunCardData = {
        run_id: (message.meta?.run_id as string) || message.id,
        status: (message.meta?.status as RunStatus) || "running",
        node: (message.meta?.node as string) || undefined,
        provider: (message.meta?.provider as string) || undefined,
        elapsed_seconds:
          typeof message.meta?.elapsed_seconds === "number" ? message.meta.elapsed_seconds : undefined,
        log_tail:
          (message.meta?.log_tail as string | string[]) || (message.body_md ? [message.body_md] : []),
        pr: (message.meta?.pr as number | string) || undefined,
        pr_url: (message.meta?.pr_url as string) || undefined,
        run_url: (message.meta?.run_url as string) || undefined,
        can_cancel: typeof message.meta?.can_cancel === "boolean" ? message.meta.can_cancel : undefined,
      };
      return <RunCard run={runData} onCancelRun={onCancelRun} />;
    }

    if (message.kind === "handoff") {
      const handoffData: HandoffCardData = {
        from_role: (message.meta?.from_role as string) || message.author || "barb",
        to_role: (message.meta?.to_role as string) || "librarian",
        reason: (message.meta?.reason as string) || message.body_md || "",
        thread_id: message.thread_id,
        available_roles:
          (message.meta?.available_roles as Array<{ id: string; name: string }>) || undefined,
      };
      return <HandoffCard handoff={handoffData} onRedirectHandoff={onRedirectHandoff} />;
    }

    if (message.kind === "review" || message.kind === "review_verdict") {
      const reviewData: ReviewCardData = {
        pr_number: (message.meta?.pr_number as number | string) || undefined,
        pr_title: (message.meta?.pr_title as string) || undefined,
        pr_url: (message.meta?.pr_url as string) || undefined,
        verdict: (message.meta?.verdict as ReviewVerdict) || "COMMENTED",
        summary: (message.meta?.summary as string) || message.body_md || undefined,
        findings: (message.meta?.findings as string[]) || [],
        reviewer: (message.meta?.reviewer as string) || message.author || undefined,
        reviewed_at: (message.meta?.reviewed_at as string) || message.created_at || undefined,
      };
      return <ReviewCard review={reviewData} />;
    }

    // Default: chat bubble with markdown
    return (
      <div
        className={`thread-bubble ${isUser ? "thread-bubble--user" : "thread-bubble--agent"}`}
        style={{
          background: isUser ? "var(--accent-blue, #1f6feb)" : "var(--bg-tertiary, #161b22)",
          color: isUser ? "#fff" : "var(--text-primary, #c9d1d9)",
          border: isUser ? "none" : "1px solid var(--border, #30363d)",
          borderRadius: isUser ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
          padding: "10px 14px",
          maxWidth: "85%",
          wordBreak: "break-word",
          fontSize: 13,
          lineHeight: 1.5,
        }}
      >
        {isUser ? (
          <div style={{ whiteSpace: "pre-wrap" }}>{message.body_md}</div>
        ) : (
          <ThreadMarkdown content={message.body_md} />
        )}

        {/* Streaming Indicator & Stop Button */}
        {isActivelyStreaming && (
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              marginTop: 6,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                color: isUser ? "rgba(255, 255, 255, 0.7)" : "var(--accent-blue, #58a6ff)",
                animation: "pulse 1s infinite",
                fontWeight: 900,
              }}
            >
              ▌
            </span>
            {onStopStreaming && (
              <button
                type="button"
                aria-label="Stop generating"
                onClick={() => onStopStreaming(message.id)}
                style={{
                  background: "rgba(248, 81, 73, 0.15)",
                  border: "1px solid rgba(248, 81, 73, 0.4)",
                  borderRadius: 4,
                  color: "var(--accent-red, #f85149)",
                  cursor: "pointer",
                  fontSize: 11,
                  fontWeight: 600,
                  padding: "2px 8px",
                }}
              >
                ■ Stop
              </button>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      className={`thread-message-item ${isUser ? "thread-message-item--user" : "thread-message-item--agent"}`}
      data-testid={`message-${message.id}`}
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: isUser ? "flex-end" : "flex-start",
        margin: "8px 0",
        maxWidth: "100%",
      }}
    >
      {/* Header with Author and Time */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 4,
          fontSize: 11,
          color: "var(--text-muted, #8b949e)",
        }}
      >
        <span
          style={{
            fontWeight: 600,
            color: isUser ? "var(--text-secondary, #c9d1d9)" : "var(--accent-blue, #58a6ff)",
          }}
        >
          {isUser ? "You" : message.author.charAt(0).toUpperCase() + message.author.slice(1)}
        </span>
        {timeStr && <span>{timeStr}</span>}
        {message.delivery === "pending" && !isActivelyStreaming && (
          <span style={{ fontStyle: "italic" }}>sending…</span>
        )}
      </div>

      {renderCardContent()}
    </div>
  );
};
