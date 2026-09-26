/**
 * MessageItem.tsx — Renders individual thread messages: user bubbles,
 * staff markdown with code copying and link previews, streaming deltas with stop button,
 * and embedded interactive cards: Action, Run, Handoff, Review, and Error (SC-D4, SC-D5, Issues #1318, #1319).
 */
import React from "react";
import type { ThreadMessage } from "./threadTypes";
import { ThreadMarkdown } from "./threadMarkdown";
import { formatMessageTime } from "./threadUtils";
import { GroupDeliberationCard } from "./GroupDeliberationCard";
import { parseGroupTurn } from "./groupTurn";
import {
  ActionCard,
  type ActionProposalData,
  type ActionRiskLevel,
  ErrorCard,
  HandoffCard,
  type HandoffCardData,
  type ProposalApproveHandler,
  type ProposalDenyHandler,
  type ProposalStatus,
  ReviewCard,
  type ReviewCardData,
  type ReviewVerdict,
  RunCard,
  type RunCancelHandler,
  type RunCardData,
  type RunStatus,
} from "./cards";

export interface MessageItemProps {
  message: ThreadMessage;
  isStreaming?: boolean;
  onStopStreaming?: (messageId: string) => void;
  onRetry?: (message: ThreadMessage) => void;
  onApproveProposal?: ProposalApproveHandler;
  onDenyProposal?: ProposalDenyHandler;
  onCancelRun?: RunCancelHandler;
  /** Answer a needs-input run in this message's thread (#1547). */
  onAnswerRun?: (threadId: string, runId: string, answer: string) => Promise<boolean>;
  onRerouteHandoff?: (targetRole: string) => void;
}

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isStreaming = false,
  onStopStreaming,
  onRetry,
  onApproveProposal,
  onDenyProposal,
  onCancelRun,
  onAnswerRun,
  onRerouteHandoff,
}) => {
  const isUser = message.author_kind === "user" || message.author === "user";
  const isFailed = message.delivery === "failed" || message.kind === "error";
  const isActivelyStreaming = isStreaming || message.streaming;
  const timeStr = formatMessageTime(message.created_at);

  const renderContent = () => {
    // 1. Error Card
    if (isFailed) {
      return (
        <ErrorCard
          error={{
            failure_class: message.failure_class,
            cause: message.body_md || (message.meta?.cause as string) || message.error || "Message delivery failed",
            remediation: message.remediation || (message.meta?.remediation as string),
            node: (message.meta?.node as string) || null,
            retryable: message.meta?.retryable !== false,
          }}
          onRetry={onRetry ? () => onRetry(message) : undefined}
        />
      );
    }

    // 2. Action Proposal Card
    if (message.kind === "proposal" || message.kind === "action_proposal") {
      const proposal: ActionProposalData =
        (message.meta?.proposal as ActionProposalData) || {
          id: (message.meta?.proposal_id as string) || message.id,
          action_name: (message.meta?.action_name as string) || message.body_md || "action",
          target: message.meta?.target as string | undefined,
          params: message.meta?.params as Record<string, unknown> | undefined,
          risk_level: (message.meta?.risk_level as ActionRiskLevel) || "medium",
          status: (message.meta?.status as ProposalStatus) || "pending",
          proposed_by: (message.meta?.proposed_by as string) || message.author,
          decided_by: message.meta?.decided_by as string | null | undefined,
          decided_at: message.meta?.decided_at as string | null | undefined,
          expires_at: message.meta?.expires_at as string | null | undefined,
          description: (message.meta?.description as string) || message.body_md,
        };

      return (
        <ActionCard
          proposal={proposal}
          onApprove={onApproveProposal}
          onDeny={onDenyProposal}
        />
      );
    }

    // 3. Run Card
    if (message.kind === "run" || message.kind === "run_card") {
      const run: RunCardData =
        (message.meta?.run as RunCardData) || {
          id: (message.meta?.run_id as string) || message.id,
          run_number: (message.meta?.run_number as number | string) || undefined,
          status: (message.meta?.status as RunStatus) || "running",
          node: message.meta?.node as string | undefined,
          provider: message.meta?.provider as string | undefined,
          elapsed_seconds: message.meta?.elapsed_seconds as number | undefined,
          logs_tail: message.meta?.logs_tail as string[] | undefined,
          pr_number: message.meta?.pr_number as number | string | undefined,
          pr_url: message.meta?.pr_url as string | undefined,
          run_url: message.meta?.run_url as string | undefined,
        };

      return (
        <RunCard
          run={run}
          onCancel={onCancelRun}
          onAnswer={onAnswerRun && ((runId, answer) => onAnswerRun(message.thread_id, runId, answer))}
        />
      );
    }

    // 4. Handoff Card
    if (message.kind === "handoff") {
      const handoff: HandoffCardData =
        (message.meta?.handoff as HandoffCardData) || {
          from_role: (message.meta?.from_role as string) || message.author || "barb",
          to_role: (message.meta?.to_role as string) || "specialist",
          reason: (message.meta?.reason as string) || message.body_md || "",
          available_alternatives: message.meta?.available_alternatives as Array<{ name: string; title: string }> | undefined,
        };

      return (
        <HandoffCard
          handoff={handoff}
          onReroute={onRerouteHandoff}
        />
      );
    }

    // 5. Review Card
    if (message.kind === "review") {
      const review: ReviewCardData =
        (message.meta?.review as ReviewCardData) || {
          pr_number: (message.meta?.pr_number as number | string) || "PR",
          pr_title: message.meta?.pr_title as string | undefined,
          pr_url: message.meta?.pr_url as string | undefined,
          verdict: (message.meta?.verdict as ReviewVerdict) || "commented",
          summary: (message.meta?.summary as string) || message.body_md,
          findings: message.meta?.findings as string[] | undefined,
        };

      return (
        <ReviewCard
          review={review}
        />
      );
    }

    // 6. Board group turn (SC-D7, #1342); the pending placeholder falls through to the bubble.
    const groupTurn = parseGroupTurn(message);
    if (groupTurn) {
      return <GroupDeliberationCard message={message} turn={groupTurn} />;
    }

    // Default: Chat Bubble with Markdown or Plain Text
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
              className="thread-streaming-cursor"
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
        <span style={{ fontWeight: 600, color: isUser ? "var(--text-secondary, #c9d1d9)" : "var(--accent-blue, #58a6ff)" }}>
          {isUser ? "You" : message.author.charAt(0).toUpperCase() + message.author.slice(1)}
        </span>
        {timeStr && <span>{timeStr}</span>}
        {message.delivery === "pending" && !isActivelyStreaming && (
          <span style={{ fontStyle: "italic" }}>sending…</span>
        )}
      </div>

      {renderContent()}
    </div>
  );
};
