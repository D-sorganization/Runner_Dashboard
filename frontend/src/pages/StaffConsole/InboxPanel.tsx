/**
 * InboxPanel.tsx — "Waiting on you" inbox panel and Barb briefing launcher.
 *
 * Implements SC-C5 (Issue #1328) under Epic SC-C (#1349) / Umbrella #1354.
 */

import React, { useMemo, useState } from "react";
import type {
  InboxPanelProps,
  WaitingOnYouCategory,
  WaitingOnYouItem,
} from "./inboxTypes";
import "./inboxPanel.css";

const CATEGORY_TABS: { key: WaitingOnYouCategory; label: string; countKey?: string }[] = [
  { key: "all", label: "All" },
  { key: "approval", label: "Approvals", countKey: "approvals" },
  { key: "question", label: "Questions", countKey: "questions" },
  { key: "escalation", label: "Escalations", countKey: "escalations" },
  { key: "project_decision", label: "Project Decisions", countKey: "project_decisions" },
  { key: "board_proposal", label: "Board Proposals", countKey: "board_proposals" },
  { key: "auth_signin", label: "Auth Sign-in", countKey: "auth_signins" },
];

function getCategoryIcon(cat: string): string {
  switch (cat) {
    case "approval":
      return "✍️";
    case "question":
      return "❓";
    case "escalation":
      return "🚨";
    case "project_decision":
      return "🎯";
    case "board_proposal":
      return "🏛️";
    case "auth_signin":
      return "🔑";
    default:
      return "📌";
  }
}

function getCategoryLabel(cat: string): string {
  switch (cat) {
    case "approval":
      return "Approval";
    case "question":
      return "Question";
    case "escalation":
      return "Escalation";
    case "project_decision":
      return "Decision";
    case "board_proposal":
      return "Board";
    case "auth_signin":
      return "Auth";
    default:
      return cat;
  }
}

export const InboxPanel: React.FC<InboxPanelProps> = ({
  inbox,
  isLoading = false,
  onRefresh,
  onGenerateBriefing,
  isGeneratingBriefing = false,
  onOpenItem,
  className = "",
}) => {
  const [activeTab, setActiveTab] = useState<WaitingOnYouCategory>("all");
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const items = useMemo(() => inbox?.items ?? [], [inbox?.items]);
  const counts = useMemo(() => inbox?.counts ?? {}, [inbox?.counts]);
  const totalCount = inbox?.count ?? items.length;

  const filteredItems = useMemo(() => {
    if (activeTab === "all") return items;
    return items.filter((it) => it.category === activeTab);
  }, [items, activeTab]);

  const unavailableSources = useMemo(() => {
    if (!inbox?.sources) return [];
    return Object.entries(inbox.sources)
      .filter(([, status]) => status.status === "unavailable")
      .map(([name, status]) => ({ name, error: status.error }));
  }, [inbox]);

  const handleCopy = (id: string, text: string) => {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    }
  };

  return (
    <section
      className={`staff-inbox-panel ${className}`}
      role="region"
      aria-label="Waiting on you inbox"
    >
      <header className="staff-inbox-panel__header">
        <div className="staff-inbox-panel__title-wrap">
          <h2 className="staff-inbox-panel__title">Waiting on You</h2>
          <span
            className={`staff-inbox-panel__badge ${
              totalCount === 0 ? "staff-inbox-panel__badge--zero" : ""
            }`}
            aria-label={`${totalCount} items waiting`}
          >
            {totalCount}
          </span>
        </div>

        <div className="staff-inbox-panel__actions">
          {onGenerateBriefing && (
            <button
              type="button"
              className="staff-inbox-panel__btn staff-inbox-panel__btn--primary"
              onClick={() => onGenerateBriefing()}
              disabled={isGeneratingBriefing}
              aria-label="Generate Barb briefing"
            >
              {isGeneratingBriefing ? "Generating..." : "📋 /brief"}
            </button>
          )}

          {onRefresh && (
            <button
              type="button"
              className="staff-inbox-panel__btn"
              onClick={onRefresh}
              disabled={isLoading}
              aria-label="Refresh inbox"
            >
              🔄 Refresh
            </button>
          )}
        </div>
      </header>

      {unavailableSources.length > 0 && (
        <div className="staff-inbox-panel__warning-banner" role="alert">
          <span>⚠️</span>
          <span>
            Some sources unavailable:{" "}
            {unavailableSources.map((s) => s.name).join(", ")}. Remaining items
            rendered successfully.
          </span>
        </div>
      )}

      <nav
        className="staff-inbox-panel__filter-bar"
        aria-label="Filter inbox by category"
      >
        {CATEGORY_TABS.map((tab) => {
          const tabCount =
            tab.key === "all"
              ? totalCount
              : tab.countKey
              ? counts[tab.countKey] ?? 0
              : 0;
          const isActive = activeTab === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              className={`staff-inbox-panel__filter-tab ${
                isActive ? "staff-inbox-panel__filter-tab--active" : ""
              }`}
              onClick={() => setActiveTab(tab.key)}
              aria-pressed={isActive}
            >
              <span>{tab.label}</span>
              <span>({tabCount})</span>
            </button>
          );
        })}
      </nav>

      {filteredItems.length === 0 ? (
        <div className="staff-inbox-panel__empty">
          <span className="staff-inbox-panel__empty-icon" role="img" aria-label="sparkles">
            ✨
          </span>
          <p>
            {totalCount === 0
              ? "All clear! Nothing currently waiting on you."
              : `No items in ${activeTab}.`}
          </p>
        </div>
      ) : (
        <ul className="staff-inbox-panel__list">
          {filteredItems.map((item: WaitingOnYouItem) => {
            const isCritical = item.severity === "critical";
            const isHigh = item.severity === "high";
            const loginCommand = item.metadata?.login_command as string | undefined;

            return (
              <li
                key={item.id}
                className={`staff-inbox-panel__item ${
                  isCritical
                    ? "staff-inbox-panel__item--critical"
                    : isHigh
                    ? "staff-inbox-panel__item--high"
                    : ""
                }`}
              >
                <div className="staff-inbox-panel__item-top">
                  <span className="staff-inbox-panel__category-badge">
                    <span>{getCategoryIcon(item.category)}</span>
                    <span>{getCategoryLabel(item.category)}</span>
                  </span>
                  <span className="staff-inbox-panel__severity-label">
                    {item.severity}
                  </span>
                </div>

                <h3 className="staff-inbox-panel__item-title">{item.title}</h3>
                <p className="staff-inbox-panel__item-summary">{item.summary}</p>

                {loginCommand && (
                  <div style={{ marginTop: "0.25rem", display: "flex", gap: "0.5rem" }}>
                    <code style={{ fontSize: "0.75rem", background: "var(--surface-muted, #f3f4f6)", padding: "0.2rem 0.4rem", borderRadius: "4px" }}>
                      {loginCommand}
                    </code>
                    <button
                      type="button"
                      className="staff-inbox-panel__action-btn"
                      onClick={() => handleCopy(item.id, loginCommand)}
                    >
                      {copiedId === item.id ? "Copied!" : "Copy"}
                    </button>
                  </div>
                )}

                <div className="staff-inbox-panel__item-footer">
                  <span>Source: {item.source}</span>
                  {onOpenItem && (
                    <button
                      type="button"
                      className="staff-inbox-panel__action-btn"
                      onClick={() => onOpenItem(item)}
                    >
                      {item.category === "approval"
                        ? "Review & Decide →"
                        : item.category === "question"
                        ? "Answer →"
                        : item.category === "escalation"
                        ? "Inspect Escalation →"
                        : "Open →"}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
};
