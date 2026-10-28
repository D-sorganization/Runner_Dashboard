/**
 * Thread.tsx — Staff Console conversation thread view with sanitized markdown,
 * streaming token deltas, stop button, date separators, jump to unread, and composer.
 *
 * Implements SC-D4 (Issue #1318) under Epic SC-D (#1350) / Workstream B (#1720).
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import type { ThreadMessage, ThreadProps } from "./threadTypes";
import { MessageItem } from "./MessageItem";
import { Composer } from "./Composer";
import { formatSeparatorDate, getDateKey } from "./threadUtils";
import { prefersReducedMotion } from "../../design/motion";
import { groupByRound, isPanelThread } from "./panelTurn";
import "./thread.css";

/** Scroll instantly when the user asks for reduced motion (SC-D9). */
const scrollBehavior = (): ScrollBehavior => (prefersReducedMotion() ? "auto" : "smooth");

export const RoundHeader: React.FC<{ round: number }> = ({ round }) => {
  return (
    <div
      role="separator"
      className="panel-round-header"
      aria-label={`Round ${round}`}
    >
      <div className="thread-separator-line" />
      <span className="thread-separator-label" style={{ color: "var(--accent-blue, #58a6ff)" }}>Round {round}</span>
      <div className="thread-separator-line" />
    </div>
  );
};

export const DateSeparator: React.FC<{ label: string }> = ({ label }) => {
  return (
    <div
      role="separator"
      className="thread-date-separator"
    >
      <div className="thread-separator-line" />
      <span className="thread-separator-label">{label}</span>
      <div className="thread-separator-line" />
    </div>
  );
};

type GroupedThreadItem = {
  type: "separator" | "message" | "round_header";
  key: string;
  label?: string;
  round?: number;
  message?: ThreadMessage;
};

const VIRTUALIZE_AFTER_MESSAGES = 80;
const VIRTUAL_OVERSCAN_PX = 640;
const ESTIMATED_MESSAGE_HEIGHT = 176;
const ESTIMATED_SEPARATOR_HEIGHT = 48;

export const Thread: React.FC<ThreadProps> = ({
  thread,
  messages = [],
  unreadSeqThreshold,
  isReconnecting = false,
  onStopStreaming,
  onRetryMessage,
  onSendMessage,
  roles = [],
  className = "",
  onApproveProposal,
  onDenyProposal,
  onCancelRun,
  onAnswerRun,
  onRerouteHandoff,
  onFollowHandoff,
}) => {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const unreadTargetRef = useRef<HTMLDivElement>(null);
  const [showJumpToUnread, setShowJumpToUnread] = useState<boolean>(false);
  const [userScrolledUp, setUserScrolledUp] = useState<boolean>(false);
  const [virtualScrollTop, setVirtualScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);
  const [measuredHeights, setMeasuredHeights] = useState<ReadonlyMap<string, number>>(() => new Map());
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const observedRowsRef = useRef(new Map<string, HTMLDivElement>());
  const rowRefCache = useRef(new Map<string, (node: HTMLDivElement | null) => void>());

  const isPanel = isPanelThread(thread);

  // Group messages by date or by round (for panel threads)
  const groupedItems = useMemo(() => {
    if (isPanel) {
      const roundGroups = groupByRound(messages);
      const roundFirstMessageId = new Map<number, string>();
      for (const rg of roundGroups) {
        if (rg.messages.length > 0) {
          roundFirstMessageId.set(rg.round, rg.messages[0].id);
        }
      }
      const messageToRoundStart = new Map<string, number>();
      for (const [r, msgId] of roundFirstMessageId.entries()) {
        messageToRoundStart.set(msgId, r);
      }

      const elements: GroupedThreadItem[] = [];

      for (let i = 0; i < messages.length; i++) {
        const msg = messages[i];
        if (messageToRoundStart.has(msg.id)) {
          const r = messageToRoundStart.get(msg.id)!;
          elements.push({
            type: "round_header",
            key: `round-${r}`,
            label: `Round ${r}`,
            round: r,
          });
        }
        elements.push({
          type: "message",
          key: `msg-${msg.id}`,
          message: msg,
        });
      }
      return elements;
    }

    const elements: GroupedThreadItem[] = [];
    let lastDateKey = "";

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      const dateKey = getDateKey(msg.created_at);

      if (dateKey !== lastDateKey && dateKey !== "unknown") {
        elements.push({
          type: "separator",
          key: `sep-${dateKey}-${i}`,
          label: formatSeparatorDate(msg.created_at),
        });
        lastDateKey = dateKey;
      }

      elements.push({
        type: "message",
        key: `msg-${msg.id}`,
        message: msg,
      });
    }

    return elements;
  }, [messages, isPanel]);

  const itemOffsets = useMemo(() => {
    const offsets = [0];
    for (const item of groupedItems) {
      const measured = measuredHeights.get(item.key);
      const estimate = item.message
        ? ESTIMATED_MESSAGE_HEIGHT + Math.min(800, Math.ceil((item.message.body_md?.length || 0) / 240) * 24)
        : ESTIMATED_SEPARATOR_HEIGHT;
      offsets.push(offsets[offsets.length - 1] + (measured || estimate));
    }
    return offsets;
  }, [groupedItems, measuredHeights]);

  const useVirtualization = messages.length > VIRTUALIZE_AFTER_MESSAGES;
  const totalVirtualHeight = itemOffsets[itemOffsets.length - 1] || 0;
  let virtualStart = 0;
  let virtualEnd = groupedItems.length;
  if (useVirtualization) {
    const startAt = Math.max(0, virtualScrollTop - VIRTUAL_OVERSCAN_PX);
    const endAt = virtualScrollTop + viewportHeight + VIRTUAL_OVERSCAN_PX;
    while (virtualStart < groupedItems.length - 1 && itemOffsets[virtualStart + 1] < startAt) virtualStart += 1;
    virtualEnd = virtualStart;
    while (virtualEnd < groupedItems.length && itemOffsets[virtualEnd] < endAt) virtualEnd += 1;
  }

  useEffect(() => {
    const container = scrollContainerRef.current;
    const observedRows = observedRowsRef.current;
    if (!container) return;
    setViewportHeight(container.clientHeight || 600);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      if (entries.some((entry) => entry.target === container)) {
        setViewportHeight(container.clientHeight || 600);
      }
      setMeasuredHeights((current) => {
        let next: Map<string, number> | null = null;
        for (const entry of entries) {
          if (entry.target === container) continue;
          const key = (entry.target as HTMLElement).dataset.threadVirtualItem;
          const height = (entry.target as HTMLElement).getBoundingClientRect().height;
          if (!key || height <= 0 || Math.abs((current.get(key) || 0) - height) < 1) continue;
          next ??= new Map(current);
          next.set(key, height);
        }
        return next || current;
      });
    });
    resizeObserverRef.current = observer;
    observer.observe(container);
    for (const row of observedRows.values()) observer.observe(row);
    return () => {
      observer.disconnect();
      resizeObserverRef.current = null;
      observedRows.clear();
    };
  }, []);

  const virtualRowRef = (key: string) => {
    let callback = rowRefCache.current.get(key);
    if (!callback) {
      callback = (node) => {
        const previous = observedRowsRef.current.get(key);
        if (previous && previous !== node) {
          resizeObserverRef.current?.unobserve(previous);
          observedRowsRef.current.delete(key);
        }
        if (node) {
          observedRowsRef.current.set(key, node);
          resizeObserverRef.current?.observe(node);
        }
      };
      rowRefCache.current.set(key, callback);
    }
    return callback;
  };

  // Check if unread messages exist
  const firstUnread = useMemo(() => {
    if (unreadSeqThreshold === undefined) return null;
    return messages.find((m) => (m.seq ?? 0) >= unreadSeqThreshold) || null;
  }, [messages, unreadSeqThreshold]);

  const renderGroupedItem = (item: GroupedThreadItem) => {
    if (item.type === "round_header" && item.round != null) {
      return <RoundHeader key={item.key} round={item.round} />;
    }
    if (item.type === "separator") {
      return <DateSeparator key={item.key} label={item.label || ""} />;
    }
    if (!item.message) return null;

    const isUnreadTarget = firstUnread && item.message.id === firstUnread.id;
    const msgIndex = messages.findIndex((message) => message.id === item.message?.id);
    const prevMsg = msgIndex > 0 ? messages[msgIndex - 1] : null;
    const isSameAuthor = prevMsg && prevMsg.author === item.message.author;
    const timeDiffMs =
      prevMsg && item.message.created_at && prevMsg.created_at
        ? Math.abs(new Date(item.message.created_at).getTime() - new Date(prevMsg.created_at).getTime())
        : Infinity;
    const showHeader = !(isSameAuthor && timeDiffMs < 5 * 60 * 1000);

    return (
      <div
        key={item.key}
        ref={isUnreadTarget ? unreadTargetRef : undefined}
        className={isUnreadTarget ? "thread-unread-target" : undefined}
        style={{ width: "100%" }}
      >
        <MessageItem
          message={item.message}
          isStreaming={item.message.streaming}
          showHeader={showHeader}
          onStopStreaming={onStopStreaming}
          onRetry={onRetryMessage}
          onApproveProposal={onApproveProposal}
          onDenyProposal={onDenyProposal}
          onCancelRun={onCancelRun}
          onAnswerRun={onAnswerRun}
          onRerouteHandoff={onRerouteHandoff}
          onFollowHandoff={onFollowHandoff}
        />
      </div>
    );
  };

  useEffect(() => {
    if (firstUnread) {
      setShowJumpToUnread(true);
    } else {
      setShowJumpToUnread(false);
    }
  }, [firstUnread]);

  // Auto-scroll to bottom on new messages unless user manually scrolled up
  useEffect(() => {
    if (!userScrolledUp && scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
      setVirtualScrollTop(scrollContainerRef.current.scrollTop);
    }
  }, [messages, userScrolledUp, totalVirtualHeight]);

  const handleScroll = (event: React.UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    if (useVirtualization) setVirtualScrollTop(el.scrollTop);
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    setUserScrolledUp(!isAtBottom);

    if (isAtBottom && showJumpToUnread) {
      setShowJumpToUnread(false);
    }
  };

  const handleJumpToUnread = () => {
    setShowJumpToUnread(false);
    const behavior = scrollBehavior();
    if (unreadTargetRef.current && typeof unreadTargetRef.current.scrollIntoView === "function") {
      unreadTargetRef.current.scrollIntoView({ behavior, block: "start" });
    } else if (useVirtualization && firstUnread && scrollContainerRef.current) {
      const index = groupedItems.findIndex((item) => item.message?.id === firstUnread.id);
      if (index >= 0) {
        scrollContainerRef.current.scrollTop = itemOffsets[index];
        setVirtualScrollTop(itemOffsets[index]);
      }
    } else if (scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight;
    }
  };

  const handleLogKeyDown = (e: React.KeyboardEvent) => {
    const behavior = scrollBehavior();
    if (e.key === "j") {
      e.preventDefault();
      scrollContainerRef.current?.scrollBy({ top: 80, behavior });
    } else if (e.key === "k") {
      e.preventDefault();
      scrollContainerRef.current?.scrollBy({ top: -80, behavior });
    }
  };

  return (
    <div
      className={`staff-thread-view ${className}`}
      role="region"
      aria-label={`Conversation thread with ${thread.title || thread.id}`}
    >
      {/* Sticky Reconnecting Banner */}
      {isReconnecting && (
        <div
          role="status"
          className="thread-reconnecting-banner"
          style={{
            position: "sticky",
            top: 0,
            left: 0,
            right: 0,
            zIndex: 10,
            background: "var(--badge-warning-bg)",
            borderBottom: "1px solid var(--accent-yellow)",
            color: "var(--accent-yellow)",
            padding: "6px 16px",
            fontSize: 12,
            fontWeight: 600,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}
        >
          <span className="spinner" aria-hidden="true">↻</span>
          <span>Reconnecting to thread events…</span>
        </div>
      )}

      {/* Messages Scroll Area */}
      <div
        ref={scrollContainerRef}
        role="log"
        aria-label="Conversation messages"
        aria-live="polite"
        aria-relevant="additions text"
        aria-atomic="false"
        tabIndex={0}
        onScroll={handleScroll}
        onKeyDown={handleLogKeyDown}
        className="thread-messages-scroll"
      >
        {useVirtualization ? (
          <div
            className="thread-messages-container thread-messages-container--virtual"
            data-testid="thread-virtual-window"
            data-total-items={groupedItems.length}
            style={{ height: totalVirtualHeight }}
          >
            {groupedItems.slice(virtualStart, virtualEnd).map((item, visibleIndex) => {
              const index = virtualStart + visibleIndex;
              return (
                <div
                  key={item.key}
                  ref={virtualRowRef(item.key)}
                  className="thread-virtual-row"
                  data-thread-virtual-item={item.key}
                  style={{ top: itemOffsets[index] }}
                >
                  {renderGroupedItem(item)}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="thread-messages-container">
            {groupedItems.map(renderGroupedItem)}
          </div>
        )}
      </div>

      {/* Floating Jump to Unread Button */}
      {showJumpToUnread && (
        <button
          type="button"
          aria-label="Jump to unread"
          onClick={handleJumpToUnread}
          style={{
            position: "absolute",
            bottom: 84,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--accent-blue)",
            color: "var(--text-on-accent)",
            border: "none",
            borderRadius: 20,
            boxShadow: "var(--shadow-card)",
            padding: "6px 16px",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
            zIndex: 20,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span>Jump to unread</span>
          <span>↓</span>
        </button>
      )}

      {/* Composer Section */}
      {onSendMessage && (
        <Composer
          threadId={thread.id}
          roles={roles}
          onSendMessage={onSendMessage}
          focusOnThreadChange
          isPanel={isPanel}
        />
      )}
    </div>
  );
};
