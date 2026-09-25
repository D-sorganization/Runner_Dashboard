/**
 * InboxPanel.tsx — "Waiting on you" inbox and Barb briefings panel (SC-C5, Issue #1328).
 *
 * Aggregates operator attention items across multiple sources with fault isolation:
 * - Pending action proposals awaiting approval (SC-B6)
 * - Questions / clarifications needing user input (SC-B7)
 * - Work item escalations (SC-C3)
 * - Project charter decisions needed (STATUS.md)
 * - Board proposals and authentication sign-ins
 *
 * Degraded sources render a visible warning banner while healthy sources continue to display.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TimeAgo } from "../../primitives/TimeAgo";
import { fetchStaffInbox, requestStaffBriefing } from "./staffApi";
import type { InboxAggregate, InboxItem, InboxSource } from "./inboxTypes";

export interface InboxPanelProps {
  onOpenRun?: (runId: string) => void;
  className?: string;
  autoRefreshInterval?: number;
}

type FilterTab = "all" | InboxSource;

export function InboxPanel({
  onOpenRun,
  className = "",
  autoRefreshInterval = 30_000,
}: InboxPanelProps) {
  const [data, setData] = useState<InboxAggregate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<FilterTab>("all");
  const [briefingStatus, setBriefingStatus] = useState<string | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  const loadInbox = useCallback(async (signal?: AbortSignal) => {
    try {
      setError(null);
      const res = await fetchStaffInbox(signal);
      setData(res);
    } catch (err: unknown) {
      if (signal?.aborted) return;
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ac = new AbortController();
    loadInbox(ac.signal);
    const timer = setInterval(() => {
      loadInbox();
    }, autoRefreshInterval);
    return () => {
      ac.abort();
      clearInterval(timer);
    };
  }, [loadInbox, autoRefreshInterval]);

  const handleRequestBriefing = async (kind: "morning" | "evening" | "on_demand") => {
    try {
      setBriefingLoading(true);
      setBriefingStatus(null);
      const res = await requestStaffBriefing(kind);
      setBriefingStatus(
        `Briefing posted to Barb's thread (${res.waiting_count} waiting item${res.waiting_count === 1 ? "" : "s"}).`
      );
      setTimeout(() => setBriefingStatus(null), 6000);
      loadInbox();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setBriefingStatus(`Failed to request briefing: ${msg}`);
    } finally {
      setBriefingLoading(false);
    }
  };

  const unavailableSources = useMemo(() => {
    if (!data?.sources) return [];
    return Object.entries(data.sources).filter(([_, s]) => s.status === "unavailable");
  }, [data]);

  const filteredItems = useMemo(() => {
    if (!data?.items) return [];
    if (activeTab === "all") return data.items;
    return data.items.filter((item) => item.source === activeTab);
  }, [data, activeTab]);

  const severityTone = (sev: InboxItem["severity"]): "danger" | "warning" | "info" | "neutral" => {
    switch (sev) {
      case "critical":
        return "danger";
      case "high":
        return "warning";
      case "medium":
        return "info";
      default:
        return "neutral";
    }
  };

  const handleActionClick = (item: InboxItem, e: React.MouseEvent) => {
    if (item.link.startsWith("/staff?run=") && onOpenRun) {
      e.preventDefault();
      const runId = new URLSearchParams(item.link.split("?")[1] || "").get("run");
      if (runId) {
        onOpenRun(runId);
      }
    }
  };

  return (
    <section
      className={`glass-card staff-inbox-panel ${className}`}
      aria-label="Waiting on you inbox"
      data-testid="staff-inbox-panel"
    >
      <div className="staff-inbox-panel__header">
        <div className="staff-inbox-panel__title-group">
          <button
            type="button"
            className="staff-inbox-panel__collapse-toggle"
            onClick={() => setIsCollapsed((c) => !c)}
            aria-expanded={!isCollapsed}
            aria-controls="staff-inbox-content"
          >
            <span className="staff-inbox-panel__chevron">{isCollapsed ? "▶" : "▼"}</span>
            <h3 className="staff-inbox-panel__title">Waiting on You</h3>
          </button>
          {data ? (
            <Badge
              tone={data.count > 0 ? "warning" : "success"}
              size="sm"
              data-testid="inbox-total-badge"
            >
              {data.count}
            </Badge>
          ) : null}
        </div>

        <div className="staff-inbox-panel__actions">
          {briefingStatus ? (
            <span className="staff-inbox-panel__briefing-status" data-testid="briefing-status">
              {briefingStatus}
            </span>
          ) : null}
          <button
            type="button"
            className="button button--secondary button--sm"
            onClick={() => handleRequestBriefing("on_demand")}
            disabled={briefingLoading}
            data-testid="request-briefing-btn"
          >
            {briefingLoading ? "Briefing..." : "📋 Request Briefing"}
          </button>
          <button
            type="button"
            className="button button--ghost button--sm"
            onClick={() => loadInbox()}
            disabled={loading}
            aria-label="Refresh inbox"
            data-testid="refresh-inbox-btn"
          >
            ↻
          </button>
        </div>
      </div>

      {!isCollapsed && (
        <div id="staff-inbox-content" className="staff-inbox-panel__content">
          {error ? (
            <div className="staff-inbox-panel__error" role="alert" data-testid="inbox-error">
              Failed to load inbox: {error}
            </div>
          ) : null}

          {/* Fault isolation: Degraded sources warning */}
          {unavailableSources.length > 0 ? (
            <div
              className="staff-inbox-panel__degraded-banner"
              role="alert"
              data-testid="inbox-degraded-banner"
            >
              <span className="staff-inbox-panel__degraded-icon">⚠️</span>
              <div>
                <strong>Some inbox sources are unavailable:</strong>
                <ul className="staff-inbox-panel__degraded-list">
                  {unavailableSources.map(([src, status]) => (
                    <li key={src}>
                      <code>{src}</code>: {status.error || "Temporarily unavailable"}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}

          {/* Filter Pills */}
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
            </div>
          ) : null}

          {/* Items list */}
          {loading && !data ? (
            <p className="staff-muted">Loading inbox items...</p>
          ) : filteredItems.length === 0 ? (
            <div className="staff-inbox-panel__empty" data-testid="inbox-empty">
              🎉 All clear! Nothing is currently waiting on you.
            </div>
          ) : (
            <ul className="staff-inbox-panel__list" data-testid="inbox-item-list">
              {filteredItems.map((item) => (
                <li
                  key={item.id}
                  className={`staff-inbox-item staff-inbox-item--${item.severity}`}
                  data-testid={`inbox-item-${item.id}`}
                >
                  <div className="staff-inbox-item__main">
                    <div className="staff-inbox-item__tags">
                      <Badge tone={severityTone(item.severity)} size="sm">
                        {item.severity.toUpperCase()}
                      </Badge>
                      <Badge tone="neutral" size="sm">
                        {item.source.replace("_", " ")}
                      </Badge>
                      <span className="staff-inbox-item__time">
                        <TimeAgo iso={item.created_at} live />
                      </span>
                    </div>
                    <h4 className="staff-inbox-item__title">{item.title}</h4>
                    <p className="staff-inbox-item__summary">{item.summary}</p>
                  </div>
                  <div className="staff-inbox-item__action-wrap">
                    <a
                      href={item.link}
                      className="button button--primary button--sm"
                      onClick={(e) => handleActionClick(item, e)}
                      data-testid={`inbox-action-${item.id}`}
                    >
                      Review →
                    </a>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

export default InboxPanel;
