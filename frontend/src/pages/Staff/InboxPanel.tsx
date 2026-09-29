/**
 * InboxPanel.tsx — "Waiting on you" Attention panel and drawer (SC-C5, Workstream D).
 *
 * Collapsed by default into a single compact attention bar above the console:
 * - "Waiting on you" + count badge + per-kind counts (Approvals 4, Decisions 73, Sign-ins 3)
 * - "Open" button opens a right-side 420px fixed drawer.
 * - Approvals sorted first, then escalations, needs-input, sign-ins and decisions.
 * - Providers needing sign-in grouped under a collapsible row.
 * - Project decisions grouped by repo.
 * - Degraded source warning with a Details disclosure.
 * - Zero emojis in chrome: clean SVG icons only.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { getFocusable } from "../../primitives/focusable";
import { TimeAgo } from "../../primitives/TimeAgo";
import { fetchStaffInbox, requestStaffBriefing } from "./staffApi";
import { inboxDetailText } from "./inboxTypes";
import type { InboxAggregate, InboxItem, InboxSource } from "./inboxTypes";
import {
  KIND_LABEL,
  MAX_DETAIL_LINES,
  SEVERITY_LABEL,
  SOURCE_PRIORITY,
  STORAGE_KEY,
  getDecisionRepo,
} from "./inboxPanelMeta";
import {
  InboxDegradedBanner,
  KindIcon,
  ChevronDownIcon,
  CheckCircleIcon,
  ClipboardIcon,
  CloseIcon,
  RefreshIcon,
} from "./inboxIcons";
import "./InboxPanel.css";

export interface InboxPanelProps {
  onOpenRun?: (runId: string) => void;
  /** Open a `/staff?thread=` link in place (#1712). */
  onOpenThread?: (threadId: string) => void;
  className?: string;
  autoRefreshInterval?: number;
  /** Force default open state (e.g. for testing). */
  defaultOpen?: boolean;
}

type FilterTab = "all" | InboxSource;

export function InboxPanel({
  onOpenRun,
  onOpenThread,
  className = "",
  autoRefreshInterval = 30_000,
  defaultOpen,
}: InboxPanelProps) {
  const [data, setData] = useState<InboxAggregate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<FilterTab>("all");
  const [briefingStatus, setBriefingStatus] = useState<string | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [signInsExpanded, setSignInsExpanded] = useState(true);
  const [expandedRepos, setExpandedRepos] = useState<Record<string, boolean>>({});

  const [isDrawerOpen, setIsDrawerOpen] = useState<boolean>(() => {
    if (defaultOpen !== undefined) return defaultOpen;
    try {
      return sessionStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  const drawerRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);

  const setDrawerOpen = useCallback((open: boolean) => {
    setIsDrawerOpen(open);
    try {
      sessionStorage.setItem(STORAGE_KEY, open ? "true" : "false");
    } catch {
      // sessionStorage unavailable
    }
  }, []);

  const loadInbox = useCallback(async (signal?: AbortSignal) => {
    try {
      setError(null);
      const res = await fetchStaffInbox(signal);
      setData(res);
    } catch (err: unknown) {
      if (signal?.aborted) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    loadInbox(ac.signal);
    const timer = setInterval(() => loadInbox(), autoRefreshInterval);
    return () => {
      ac.abort();
      clearInterval(timer);
    };
  }, [loadInbox, autoRefreshInterval]);

  useEffect(() => {
    if (!isDrawerOpen) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeBtnRef.current?.focus();
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setDrawerOpen(false);
        return;
      }
      // Keep Tab / Shift+Tab inside the modal drawer (aria-modal).
      if (e.key !== "Tab" || !drawerRef.current) return;
      const focusable = getFocusable(drawerRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      opener?.focus();
    };
  }, [isDrawerOpen, setDrawerOpen]);

  const handleRequestBriefing = async (kind: "morning" | "evening" | "on_demand") => {
    try {
      setBriefingLoading(true);
      setBriefingStatus(null);
      const res = await requestStaffBriefing(kind);
      setBriefingStatus(`Briefing posted to Barb's thread (${res.waiting_count} waiting items).`);
      setTimeout(() => setBriefingStatus(null), 6000);
      loadInbox();
    } catch (err: unknown) {
      setBriefingStatus(`Failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setBriefingLoading(false);
    }
  };

  const unavailableSources = useMemo(() => {
    if (!data?.sources) return [];
    return Object.entries(data.sources).filter(([_, s]) => s.status === "unavailable");
  }, [data]);

  const sortedItems = useMemo(() => {
    if (!data?.items) return [];
    return [...data.items].sort((a, b) => {
      const pA = SOURCE_PRIORITY[a.source] ?? 99;
      const pB = SOURCE_PRIORITY[b.source] ?? 99;
      if (pA !== pB) return pA - pB;
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });
  }, [data]);

  const filteredItems = useMemo(() => {
    if (activeTab === "all") return sortedItems;
    return sortedItems.filter((item) => item.source === activeTab);
  }, [sortedItems, activeTab]);

  const handleActionClick = (item: InboxItem, e: React.MouseEvent) => {
    if (item.link.startsWith("/staff?run=") && onOpenRun) {
      e.preventDefault();
      const runId = new URLSearchParams(item.link.split("?")[1] || "").get("run");
      if (runId) onOpenRun(runId);
    }
    if (item.link.startsWith("/staff?thread=") && onOpenThread) {
      e.preventDefault();
      const threadId = new URLSearchParams(item.link.split("?")[1] || "").get("thread");
      if (threadId) onOpenThread(threadId);
    }
  };

  const renderItemCard = (item: InboxItem) => {
    const severityLabel = SEVERITY_LABEL[item.severity];
    return (
      <li
        key={item.id}
        className={`staff-inbox-row staff-inbox-row--${item.severity}`}
        data-testid={`inbox-item-${item.id}`}
      >
        <KindIcon source={item.source} />
        <div className="staff-inbox-row__body">
          <div className="staff-inbox-row__top">
            <h4 className="staff-inbox-row__title" title={item.title}>
              {item.title}
            </h4>
            <span className="staff-inbox-row__time">
              <TimeAgo iso={item.created_at} live />
            </span>
          </div>
          <div className="staff-inbox-row__meta">
            {severityLabel ? (
              <span className={`staff-inbox-row__severity staff-inbox-row__severity--${item.severity}`}>
                {severityLabel}
              </span>
            ) : null}
            <span>{KIND_LABEL[item.source] ?? item.source}</span>
          </div>
          <p className="staff-inbox-row__summary">{item.summary}</p>
          {item.details && item.details.length > 0 ? (
            <ul className="staff-inbox-row__details" data-testid={`inbox-details-${item.id}`}>
              {item.details.slice(0, MAX_DETAIL_LINES).map((d, i) => (
                <li key={i}>{inboxDetailText(d)}</li>
              ))}
              {item.details.length > MAX_DETAIL_LINES ? (
                <li className="staff-inbox-row__details-more">+{item.details.length - MAX_DETAIL_LINES} more</li>
              ) : null}
            </ul>
          ) : null}
        </div>
        <a
          href={item.link}
          className="staff-inbox-row__action"
          onClick={(e) => handleActionClick(item, e)}
          data-testid={`inbox-action-${item.id}`}
        >
          Review
        </a>
      </li>
    );
  };

  const authItems = useMemo(() => filteredItems.filter((i) => i.source === "auth_sign_in"), [filteredItems]);
  const nonAuthItems = useMemo(() => filteredItems.filter((i) => i.source !== "auth_sign_in"), [filteredItems]);

  const decisionGroups = useMemo(() => {
    const decs = nonAuthItems.filter((i) => i.source === "project_decision");
    const map = new Map<string, InboxItem[]>();
    for (const d of decs) {
      const r = getDecisionRepo(d);
      const list = map.get(r) || [];
      list.push(d);
      map.set(r, list);
    }
    return map;
  }, [nonAuthItems]);

  const otherItems = useMemo(() => nonAuthItems.filter((i) => i.source !== "project_decision"), [nonAuthItems]);

  return (
    <aside className={`staff-inbox-panel ${className}`} aria-label="Waiting on you inbox" data-testid="staff-inbox-panel">
      <div className="staff-inbox-bar">
        <div className="staff-inbox-bar__main">
          <div className="staff-inbox-bar__title-group">
            <h3 className="staff-inbox-bar__title">Waiting on You</h3>
            {data ? (
              <Badge tone={data.count > 0 ? "warning" : "success"} size="sm" data-testid="inbox-total-badge">
                {data.count}
              </Badge>
            ) : null}
          </div>
          {data && data.count > 0 ? (
            <div className="staff-inbox-bar__kind-counts" data-testid="inbox-kind-counts">
              {data.counts.approvals > 0 ? <span className="staff-inbox-bar__chip">Approvals {data.counts.approvals}</span> : null}
              {data.counts.escalations > 0 ? <span className="staff-inbox-bar__chip">Escalations {data.counts.escalations}</span> : null}
              {data.counts.needs_input > 0 ? <span className="staff-inbox-bar__chip">Needs input {data.counts.needs_input}</span> : null}
              {data.counts.auth_sign_ins > 0 ? <span className="staff-inbox-bar__chip">Sign-ins {data.counts.auth_sign_ins}</span> : null}
              {data.counts.project_decisions > 0 ? <span className="staff-inbox-bar__chip">Decisions {data.counts.project_decisions}</span> : null}
              {data.counts.board_proposals > 0 ? <span className="staff-inbox-bar__chip">Proposals {data.counts.board_proposals}</span> : null}
            </div>
          ) : data ? (
            <span className="staff-inbox-bar__all-clear">All clear</span>
          ) : null}
        </div>
        <div className="staff-inbox-bar__actions">
          {briefingStatus ? (
            <span className="staff-inbox-panel__briefing-status" data-testid="briefing-status">{briefingStatus}</span>
          ) : null}
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => handleRequestBriefing("on_demand")}
            disabled={briefingLoading}
            data-testid="request-briefing-btn"
          >
            <ClipboardIcon />
            <span>{briefingLoading ? "Briefing..." : "Request Briefing"}</span>
          </button>
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => loadInbox()}
            disabled={loading}
            aria-label="Refresh inbox"
            data-testid="refresh-inbox-btn"
          >
            <RefreshIcon />
          </button>
          <button
            type="button"
            className="button button--secondary button--sm staff-inbox-bar__open-btn"
            onClick={() => setDrawerOpen(true)}
            aria-expanded={isDrawerOpen}
            aria-controls="staff-inbox-drawer"
            data-testid="inbox-open-btn"
          >
            Open
          </button>
        </div>
      </div>
      {unavailableSources.length > 0 ? <InboxDegradedBanner unavailableSources={unavailableSources} /> : null}

      {isDrawerOpen ? (
        <>
          <div
            className="staff-inbox-drawer__backdrop"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
            data-testid="inbox-drawer-backdrop"
          />
          <div
            id="staff-inbox-drawer"
            className="staff-inbox-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Waiting on you inbox"
            data-testid="staff-inbox-drawer"
            ref={drawerRef}
          >
            <div className="staff-inbox-drawer__header">
              <div className="staff-inbox-drawer__title-group">
                <h3 className="staff-inbox-drawer__title">Waiting on you</h3>
                {data ? <span className="staff-inbox-drawer__count">{data.count}</span> : null}
              </div>
              <button
                ref={closeBtnRef}
                type="button"
                className="button button--ghost button--sm staff-inbox-drawer__close"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close inbox drawer"
                data-testid="inbox-close-btn"
              >
                <CloseIcon />
              </button>
            </div>

            <div className="staff-inbox-drawer__content">
              {error ? <div className="staff-inbox-panel__error" role="alert" data-testid="inbox-error">Failed: {error}</div> : null}

              {data && data.items.length > 0 ? (
                <div className="staff-inbox-tabs" role="tablist" aria-label="Inbox filters">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "all"}
                    className={`staff-inbox-tab${activeTab === "all" ? " staff-inbox-tab--active" : ""}`}
                    onClick={() => setActiveTab("all")}
                    data-testid="filter-tab-all"
                  >
                    All <span className="staff-inbox-tab__count">{data.count}</span>
                  </button>
                  {data.counts.approvals > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "approval"}
                      className={`staff-inbox-tab${activeTab === "approval" ? " staff-inbox-tab--active" : ""}`}
                      onClick={() => setActiveTab("approval")}
                      data-testid="filter-tab-approval"
                    >
                      Approvals <span className="staff-inbox-tab__count">{data.counts.approvals}</span>
                    </button>
                  ) : null}
                  {data.counts.needs_input > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "needs_input"}
                      className={`staff-inbox-tab${activeTab === "needs_input" ? " staff-inbox-tab--active" : ""}`}
                      onClick={() => setActiveTab("needs_input")}
                      data-testid="filter-tab-needs-input"
                    >
                      Needs input <span className="staff-inbox-tab__count">{data.counts.needs_input}</span>
                    </button>
                  ) : null}
                  {data.counts.escalations > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "escalation"}
                      className={`staff-inbox-tab${activeTab === "escalation" ? " staff-inbox-tab--active" : ""}`}
                      onClick={() => setActiveTab("escalation")}
                      data-testid="filter-tab-escalation"
                    >
                      Escalations <span className="staff-inbox-tab__count">{data.counts.escalations}</span>
                    </button>
                  ) : null}
                  {data.counts.project_decisions > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "project_decision"}
                      className={`staff-inbox-tab${activeTab === "project_decision" ? " staff-inbox-tab--active" : ""}`}
                      onClick={() => setActiveTab("project_decision")}
                      data-testid="filter-tab-project-decision"
                    >
                      Decisions <span className="staff-inbox-tab__count">{data.counts.project_decisions}</span>
                    </button>
                  ) : null}
                  {data.counts.board_proposals > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "board_proposal"}
                      className={`staff-inbox-tab${activeTab === "board_proposal" ? " staff-inbox-tab--active" : ""}`}
                      onClick={() => setActiveTab("board_proposal")}
                      data-testid="filter-tab-board-proposal"
                    >
                      Proposals <span className="staff-inbox-tab__count">{data.counts.board_proposals}</span>
                    </button>
                  ) : null}
                </div>
              ) : null}

              {loading && !data ? (
                <p className="staff-muted">Loading inbox items...</p>
              ) : filteredItems.length === 0 ? (
                <div className="staff-inbox-panel__empty" data-testid="inbox-empty">
                  <CheckCircleIcon /> All clear! Nothing is waiting on you.
                </div>
              ) : (
                <ul className="staff-inbox-panel__list" data-testid="inbox-item-list">
                  {otherItems.map(renderItemCard)}

                  {authItems.length > 1 ? (
                    <li className="staff-inbox-group">
                      <button
                        type="button"
                        className="staff-inbox-group__toggle"
                        onClick={() => setSignInsExpanded((v) => !v)}
                        aria-expanded={signInsExpanded}
                      >
                        <ChevronDownIcon className={signInsExpanded ? "" : "staff-inbox-chevron--collapsed"} />
                        <span>Providers need sign-in</span>
                        <span className="staff-inbox-group__count">{authItems.length}</span>
                      </button>
                      {signInsExpanded ? (
                        <ul className="staff-inbox-group__list">
                          {authItems.map(renderItemCard)}
                        </ul>
                      ) : null}
                    </li>
                  ) : (
                    authItems.map(renderItemCard)
                  )}

                  {Array.from(decisionGroups.entries()).map(([repo, items]) => {
                    const isExp = expandedRepos[repo] ?? true;
                    return items.length > 1 ? (
                      <li key={repo} className="staff-inbox-group">
                        <button
                          type="button"
                          className="staff-inbox-group__toggle"
                          onClick={() => setExpandedRepos((prev) => ({ ...prev, [repo]: !isExp }))}
                          aria-expanded={isExp}
                        >
                          <ChevronDownIcon className={isExp ? "" : "staff-inbox-chevron--collapsed"} />
                          <span>{repo}</span>
                          <span className="staff-inbox-group__count">{items.length}</span>
                        </button>
                        {isExp ? (
                          <ul className="staff-inbox-group__list">
                            {items.map(renderItemCard)}
                          </ul>
                        ) : null}
                      </li>
                    ) : (
                      items.map(renderItemCard)
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </>
      ) : null}
    </aside>
  );
}

export default InboxPanel;
