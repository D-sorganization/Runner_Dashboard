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
import type { InboxAggregate, InboxItem, InboxSource } from "./inboxTypes";
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

const STORAGE_KEY = "staff_inbox_drawer_open";

const SOURCE_PRIORITY: Record<string, number> = {
  approval: 1,
  escalation: 2,
  needs_input: 3,
  auth_sign_in: 4,
  project_decision: 5,
  board_proposal: 6,
};

function ChevronDownIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function ClipboardIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
    </svg>
  );
}

function RefreshIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

function CloseIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function AlertTriangleIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

function CheckCircleIcon({ className = "" }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <polyline points="22 4 12 14.01 9 11.01" />
    </svg>
  );
}

function getDecisionRepo(item: InboxItem): string {
  if (typeof item.metadata?.repo === "string" && item.metadata.repo) return item.metadata.repo;
  if (item.link.includes("repo=")) {
    const r = new URLSearchParams(item.link.split("?")[1] || "").get("repo");
    if (r) return r;
  }
  const match = item.title.match(/^\[?([a-zA-Z0-9_.-]+)\]?[:-]/);
  return match ? match[1] : "General";
}

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

  const severityTone = (sev: InboxItem["severity"]): "danger" | "warning" | "info" | "neutral" => {
    switch (sev) {
      case "critical": return "danger";
      case "high": return "warning";
      case "medium": return "info";
      default: return "neutral";
    }
  };

  const renderItemCard = (item: InboxItem) => (
    <li
      key={item.id}
      className={`staff-inbox-item staff-inbox-item--${item.severity}`}
      data-testid={`inbox-item-${item.id}`}
    >
      <div className="staff-inbox-item__main">
        <div className="staff-inbox-item__tags">
          <Badge tone={severityTone(item.severity)} size="sm">{item.severity.toUpperCase()}</Badge>
          <Badge tone="neutral" size="sm">{item.source.replace("_", " ")}</Badge>
          <span className="staff-inbox-item__time"><TimeAgo iso={item.created_at} live /></span>
        </div>
        <h4 className="staff-inbox-item__title">{item.title}</h4>
        <p className="staff-inbox-item__summary">{item.summary}</p>
      </div>
      <div className="staff-inbox-item__action-wrap">
        <a
          href={item.link}
          className="button button--secondary button--sm"
          onClick={(e) => handleActionClick(item, e)}
          data-testid={`inbox-action-${item.id}`}
        >
          Review →
        </a>
      </div>
    </li>
  );

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

      {unavailableSources.length > 0 ? (
        <div className="staff-inbox-panel__degraded-banner" role="alert" data-testid="inbox-degraded-banner">
          <AlertTriangleIcon className="staff-inbox-panel__degraded-icon" />
          <div className="staff-inbox-panel__degraded-body">
            <div className="staff-inbox-panel__degraded-headline">
              {`${unavailableSources.length} source${unavailableSources.length === 1 ? "" : "s"} unavailable: ${unavailableSources.map(([src]) => src).join(", ")}`}
            </div>
            <details className="staff-inbox-panel__degraded-disclosure">
              <summary>Details</summary>
              <ul className="staff-inbox-panel__degraded-list">
                {unavailableSources.map(([src, status]) => {
                  const message = status.error || "Temporarily unavailable";
                  return (
                    <li key={src}>
                      <code>{src}</code>:{" "}
                      <span
                        className="staff-inbox-panel__degraded-error"
                        title={message}
                        data-testid={`inbox-degraded-error-${src}`}
                      >
                        {message}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </details>
          </div>
        </div>
      ) : null}

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
                <h3 className="staff-inbox-drawer__title">Attention</h3>
                {data ? <Badge tone={data.count > 0 ? "warning" : "success"} size="sm">{data.count}</Badge> : null}
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
                <div className="staff-inbox-panel__filters" role="tablist" aria-label="Inbox filters">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={activeTab === "all"}
                    className={`filter-pill ${activeTab === "all" ? "filter-pill--active" : ""}`}
                    onClick={() => setActiveTab("all")}
                    data-testid="filter-tab-all"
                  >
                    All ({data.count})
                  </button>
                  {data.counts.approvals > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "approval"}
                      className={`filter-pill ${activeTab === "approval" ? "filter-pill--active" : ""}`}
                      onClick={() => setActiveTab("approval")}
                      data-testid="filter-tab-approval"
                    >
                      Approvals ({data.counts.approvals})
                    </button>
                  ) : null}
                  {data.counts.needs_input > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "needs_input"}
                      className={`filter-pill ${activeTab === "needs_input" ? "filter-pill--active" : ""}`}
                      onClick={() => setActiveTab("needs_input")}
                      data-testid="filter-tab-needs-input"
                    >
                      Needs Input ({data.counts.needs_input})
                    </button>
                  ) : null}
                  {data.counts.escalations > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "escalation"}
                      className={`filter-pill ${activeTab === "escalation" ? "filter-pill--active" : ""}`}
                      onClick={() => setActiveTab("escalation")}
                      data-testid="filter-tab-escalation"
                    >
                      Escalations ({data.counts.escalations})
                    </button>
                  ) : null}
                  {data.counts.project_decisions > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "project_decision"}
                      className={`filter-pill ${activeTab === "project_decision" ? "filter-pill--active" : ""}`}
                      onClick={() => setActiveTab("project_decision")}
                      data-testid="filter-tab-project-decision"
                    >
                      Decisions ({data.counts.project_decisions})
                    </button>
                  ) : null}
                  {data.counts.board_proposals > 0 ? (
                    <button
                      type="button"
                      role="tab"
                      aria-selected={activeTab === "board_proposal"}
                      className={`filter-pill ${activeTab === "board_proposal" ? "filter-pill--active" : ""}`}
                      onClick={() => setActiveTab("board_proposal")}
                      data-testid="filter-tab-board-proposal"
                    >
                      Proposals ({data.counts.board_proposals})
                    </button>
                  ) : null}
                </div>
              ) : null}

              {loading && !data ? (
                <p className="staff-muted">Loading inbox items...</p>
              ) : filteredItems.length === 0 ? (
                <div className="staff-inbox-panel__empty" data-testid="inbox-empty">
                  <CheckCircleIcon /> All clear! Nothing is currently waiting on you.
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
                        <span>Providers need sign-in ({authItems.length})</span>
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
                          <span>{repo} ({items.length})</span>
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
