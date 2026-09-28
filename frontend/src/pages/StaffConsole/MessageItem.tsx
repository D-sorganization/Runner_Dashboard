/**
 * MessageItem.tsx — Renders individual thread messages: Claude-grade user bubbles,
 * full-width staff markdown prose with header grouping, avatar tint, copy action,
 * thinking indicator, and embedded cards.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350) / Workstream B (#1720).
 */
import React, { useState } from "react";
import type { ThreadMessage } from "./threadTypes";
import { ThreadMarkdown } from "./threadMarkdown";
import { formatMessageTime } from "./threadUtils";
import { GroupDeliberationCard } from "./GroupDeliberationCard";
import { parseGroupTurn } from "./groupTurn";
import { panelMeta } from "./panelTurn";
import { PanelTurnCard } from "./PanelTurnCard";
import { PanelConsensusCard } from "./PanelConsensusCard";
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
  showHeader?: boolean;
  onStopStreaming?: (messageId: string) => void;
  onRetry?: (message: ThreadMessage) => void;
  onApproveProposal?: ProposalApproveHandler;
  onDenyProposal?: ProposalDenyHandler;
  onCancelRun?: RunCancelHandler;
  /** Answer a needs-input run in this message's thread (#1547). */
  onAnswerRun?: (threadId: string, runId: string, answer: string) => Promise<boolean>;
  onRerouteHandoff?: (targetRole: string) => void;
  onFollowHandoff?: (targetRole: string) => void;
}

function getRoleTint(role: string): { bg: string; fg: string } {
  const tints = [
    { bg: "rgba(88, 166, 255, 0.15)", fg: "var(--accent-blue, #58a6ff)" },
    { bg: "rgba(188, 140, 255, 0.15)", fg: "var(--accent-purple, #bc8cff)" },
    { bg: "rgba(63, 185, 80, 0.15)", fg: "var(--accent-green, #3fb950)" },
    { bg: "rgba(240, 136, 62, 0.15)", fg: "var(--accent-orange, #f0883e)" },
    { bg: "rgba(210, 153, 34, 0.15)", fg: "var(--accent-yellow, #d29922)" },
  ];
  let hash = 0;
  for (let i = 0; i < role.length; i++) {
    hash = (hash << 5) - hash + role.charCodeAt(i);
  }
  return tints[Math.abs(hash) % tints.length];
}

export const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isStreaming = false,
  showHeader = true,
  onStopStreaming,
  onRetry,
  onApproveProposal,
  onDenyProposal,
  onCancelRun,
  onAnswerRun,
  onRerouteHandoff,
  onFollowHandoff,
}) => {
  const [copied, setCopied] = useState(false);
  const isUser = message.author_kind === "user" || message.author === "user";
  const isSystem = message.author_kind === "system" || message.kind === "system";
  const isFailed = message.delivery === "failed" || message.kind === "error";
  const isActivelyStreaming = isStreaming || message.streaming;
  const timeStr = formatMessageTime(message.created_at);
  const absoluteTime = message.created_at ? new Date(message.created_at).toLocaleString() : "";
  const roleName = message.author || "staff";
  const roleDisplayName = isUser ? "You" : roleName.charAt(0).toUpperCase() + roleName.slice(1);
  const roleInitial = roleName.charAt(0).toUpperCase();
  const roleTint = getRoleTint(roleName);

  const handleCopy = async () => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(message.body_md);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  if (isSystem) {
    return (
      <div className="thread-message-item thread-message-item--system" data-testid={`message-${message.id}`}>
        <span>{message.body_md}</span>
      </div>
    );
  }

  const renderContent = () => {
    // 0. Expert panel turn or synthesis (#1635)
    if (message.meta?.is_panel_synthesis) {
      return <PanelConsensusCard message={message} />;
    }
    const pMeta = panelMeta(message);
    if (pMeta) {
      return <PanelTurnCard message={message} meta={pMeta} />;
    }

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
          onFollow={onFollowHandoff}
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

      return <ReviewCard review={review} />;
    }

    // 6. Board group turn (SC-D7, #1342)
    const groupTurn = parseGroupTurn(message);
    if (groupTurn) {
      return <GroupDeliberationCard message={message} turn={groupTurn} />;
    }

    // User Message: rounded bubble
    if (isUser) {
      return (
        <div className="thread-bubble thread-bubble--user">
          {message.body_md}
        </div>
      );
    }

    // Staff/Assistant Message: full-width plain prose
    const isThinking = isActivelyStreaming && !message.body_md?.trim();

    return (
      <div className="thread-prose thread-prose--agent">
        {isThinking ? (
          <div className="thread-thinking-indicator" data-testid="thinking-indicator">
            <span>{roleDisplayName} is thinking…</span>
            <span className="thread-thinking-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </div>
        ) : (
          <>
            <ThreadMarkdown content={message.body_md} />
            {isActivelyStreaming && (
              <div style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 6 }}>
                <span
                  aria-hidden="true"
                  className="thread-streaming-cursor"
                  style={{
                    color: "var(--accent-blue, #58a6ff)",
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
                      background: "var(--badge-danger-bg)",
                      border: "1px solid var(--accent-red)",
                      borderRadius: 4,
                      color: "var(--accent-red)",
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
          </>
        )}
      </div>
    );
  };

  return (
    <div
      className={`thread-message-item ${isUser ? "thread-message-item--user" : "thread-message-item--agent"}`}
      data-testid={`message-${message.id}`}
    >
      {/* Header with Avatar, Author and Time (collapsible) */}
      {showHeader && !isUser && (
        <div className="thread-message-header">
          <div
            className="thread-avatar"
            style={{
              background: roleTint.bg,
              color: roleTint.fg,
            }}
            aria-hidden="true"
          >
            {roleInitial}
          </div>
          <span className="thread-author-name">{roleDisplayName}</span>
          {timeStr && (
            <span className="thread-message-time" title={absoluteTime}>
              {timeStr}
            </span>
          )}
          {message.delivery === "pending" && !isActivelyStreaming && (
            <span style={{ fontStyle: "italic", fontSize: 11, color: "var(--text-muted)" }}>
              sending…
            </span>
          )}
        </div>
      )}

      {/* Message action buttons (visible on hover / focus-within) */}
      {Boolean(message.body_md?.trim()) && (
        <div className="thread-message-actions">
          <button
            type="button"
            onClick={handleCopy}
            aria-label={copied ? "Copied" : "Copy message"}
            title="Copy message"
            className="thread-action-btn"
          >
            {copied ? (
              <>
                <svg aria-hidden="true" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                <span>Copied</span>
              </>
            ) : (
              <>
                <svg aria-hidden="true" viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      )}

      {renderContent()}
    </div>
  );
};
