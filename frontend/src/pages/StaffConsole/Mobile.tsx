/**
 * Mobile.tsx — Mobile Staff Console (SC-D8, Issue #1331).
 *
 * Implements full-screen roster → thread navigation, safe-area aware bottom
 * composer, thumb-reachable approve/deny actions, push deep linking (?thread=, ?role=),
 * and role context inspection. Console state comes from `useStaffConsole`,
 * shared with the desktop layout (#1446).
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { StaffRoleItem } from "./types";
import { ROSTER_GROUPS } from "./types";
import type { ProposalApproveHandler, ProposalDenyHandler } from "./cards/cardTypes";
import type { SendMessagePayload, ThreadInfo, ThreadMessage } from "./threadTypes";
import { Thread } from "./Thread";
import { Composer } from "./Composer";
import { GroupCostConfirm } from "./GroupCostConfirm";
import { ContextPane } from "./ContextPane";
import { ConsoleErrorBanner } from "./ConsoleErrorBanner";
import { InboxPanel } from "../Staff/InboxPanel";
import { threadKindForRole } from "./consoleThreads";
import { useStaffConsole } from "./useStaffConsole";
import "./mobile.css";

export type MobileView = "roster" | "thread" | "inbox";

export interface StaffConsoleMobileProps {
  roles?: StaffRoleItem[];
  initialView?: MobileView;
  initialRole?: string;
  initialThread?: ThreadInfo;
  initialMessages?: ThreadMessage[];
  onOpenThread?: (threadId: string) => void;
  onSendMessage?: (payload: SendMessagePayload) => Promise<{ ok: boolean; [key: string]: unknown }>;
  onApproveProposal?: ProposalApproveHandler;
  onDenyProposal?: ProposalDenyHandler;
  className?: string;
}

function parseUrlParams(): { threadId: string | null; roleName: string | null; tab: string | null } {
  if (typeof window === "undefined") return { threadId: null, roleName: null, tab: null };
  const p = new URLSearchParams(window.location.search);
  return {
    threadId: p.get("thread"),
    roleName: p.get("role"),
    tab: p.get("tab") || (p.get("inbox") ? "inbox" : null),
  };
}

export const StaffConsoleMobile: React.FC<StaffConsoleMobileProps> = ({
  roles: initialRoles,
  initialView = "roster",
  initialRole,
  initialThread,
  initialMessages,
  onOpenThread,
  onSendMessage,
  onApproveProposal,
  onDenyProposal,
  className = "",
}) => {
  const sc = useStaffConsole({
    roles: initialRoles,
    initialRole,
    initialThread,
    initialMessages,
    onSendMessage,
    onApproveProposal,
    onDenyProposal,
  });
  const { roles, selectedRole, activeThread, currentRole: currentRoleObj, roleDetail } = sc;
  const [view, setView] = useState<MobileView>(initialView);

  // SC-D9: move keyboard focus with the full-screen view change. The thread
  // heading takes focus (not the composer, which would pop the soft
  // keyboard); returning to the roster focuses the search box.
  const threadHeadingRef = useRef<HTMLHeadingElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const previousViewRef = useRef<MobileView>(initialView);
  useEffect(() => {
    const previous = previousViewRef.current;
    previousViewRef.current = view;
    if (previous === view) return;
    if (view === "thread") threadHeadingRef.current?.focus();
    else if (previous === "thread") searchInputRef.current?.focus();
  }, [view]);
  const [searchQuery, setSearchQuery] = useState("");
  const [showContext, setShowContext] = useState(false);

  // Deep Link Handling (?thread=, ?role=, ?tab=). A ?thread= id comes from a
  // push link and is real; a ?role= link resolves to the role's backend thread.
  const syncWithUrl = () => {
    const { threadId, roleName, tab } = parseUrlParams();
    if (threadId) {
      const known = initialThread?.id === threadId ? initialThread : null;
      sc.openThread(
        known ?? {
          id: threadId,
          title: roleName ? `Conversation with ${roleName}` : "Conversation",
          kind: roleName ? threadKindForRole(roleName) : "direct",
          participants: roleName ? [roleName] : [],
          status: "active",
        },
        roleName ?? undefined,
      );
      setView("thread");
      return;
    }
    if (roleName) {
      if (initialThread?.participants.includes(roleName)) {
        sc.openThread(initialThread, roleName);
        setView("thread");
      } else {
        void sc.openRole(roleName).then((thread) => thread && setView("thread"));
      }
      return;
    }
    if (tab === "inbox") setView("inbox");
  };
  const syncWithUrlRef = useRef(syncWithUrl);
  syncWithUrlRef.current = syncWithUrl;

  useEffect(() => {
    const onPopState = () => syncWithUrlRef.current();
    onPopState();
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const displayMessages = sc.messages;

  // Open a specific role conversation on its backend thread.
  const handleSelectRole = useCallback(
    async (roleName: string) => {
      const thread = await sc.openRole(roleName);
      if (!thread) return;
      setView("thread");
      onOpenThread?.(thread.id);

      if (typeof window !== "undefined") {
        const nextUrl = `?role=${encodeURIComponent(roleName)}&thread=${encodeURIComponent(thread.id)}`;
        window.history.pushState(null, "", nextUrl);
      }
    },
    [sc, onOpenThread],
  );

  // Return to roster view
  const handleBackToRoster = useCallback(() => {
    setView("roster");
    setShowContext(false);
    if (typeof window !== "undefined") {
      window.history.pushState(null, "", window.location.pathname);
    }
  }, []);

  const handleSendMessage = sc.sendMessage;
  const handleApproveProposal = sc.approveProposal;
  const handleDenyProposal = sc.denyProposal;

  // Filtered roles for roster search
  const filteredRoles = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return roles;
    return roles.filter((r) =>
      r.name.toLowerCase().includes(q) ||
      r.title.toLowerCase().includes(q) ||
      (r.summary && r.summary.toLowerCase().includes(q)),
    );
  }, [roles, searchQuery]);

  return (
    <div className={`staff-mobile ${className}`} data-testid="staff-mobile-root">
      {/* ── Thread View ──────────────────────────────────────────────────────── */}
      {view === "thread" && activeThread && (
        <div
          className="staff-mobile__content"
          data-testid="staff-mobile-thread"
          role="region"
          aria-label="Staff Conversation"
        >
          <header className="staff-mobile__header">
            <div className="staff-mobile__header-left">
              <button
                type="button"
                className="staff-mobile__back-btn"
                data-testid="staff-mobile-back-btn"
                aria-label="Back to Roster"
                onClick={handleBackToRoster}
              >
                ← Roster
              </button>
              <h1 className="staff-mobile__title" ref={threadHeadingRef} tabIndex={-1}>
                {activeThread.title}
              </h1>
            </div>
            <div className="staff-mobile__header-right">
              <button
                type="button"
                className="staff-mobile__details-btn"
                data-testid="staff-mobile-details-btn"
                aria-label="Role Details"
                onClick={() => setShowContext(true)}
              >
                ℹ
              </button>
            </div>
          </header>

          <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />

          <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
            <Thread
              thread={activeThread}
              messages={displayMessages}
              roles={roles}
              onApproveProposal={handleApproveProposal}
              onDenyProposal={handleDenyProposal}
            />
          </div>

          <div
            className="staff-mobile__composer-container"
            data-testid="staff-mobile-composer-container"
          >
            <GroupCostConfirm guard={sc.costGuard} />
            <Composer
              threadId={activeThread.id}
              roles={roles}
              selectedRole={selectedRole || undefined}
              onSendMessage={handleSendMessage}
              placeholder={`Message ${currentRoleObj.title || "staff"}…`}
            />
          </div>

          {showContext && (
            <div
              className="staff-mobile__drawer-overlay"
              data-testid="staff-mobile-context-drawer"
              role="dialog"
              aria-modal="true"
              aria-label="Role Context Details"
              onClick={() => setShowContext(false)}
            >
              <div className="staff-mobile__drawer" onClick={(e) => e.stopPropagation()}>
                <div className="staff-mobile__drawer-header">
                  <h2 style={{ margin: 0, fontSize: 16 }}>{currentRoleObj.title} Details</h2>
                  <button
                    type="button"
                    className="staff-mobile__drawer-close"
                    data-testid="staff-mobile-close-drawer"
                    aria-label="Close Details"
                    onClick={() => setShowContext(false)}
                  >
                    ✕
                  </button>
                </div>
                <ContextPane role={roleDetail} />
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Roster & Waiting on You Views ────────────────────────────────────── */}
      {view !== "thread" && (
        <div
          className="staff-mobile__content"
          data-testid="staff-mobile-roster"
          role="region"
          aria-label="Staff Roster"
        >
          <header className="staff-mobile__header">
            <h1 className="staff-mobile__title">Staff Console</h1>
          </header>

          <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />

          <div className="staff-mobile__tabs" role="tablist" aria-label="Staff views">
            <button
              type="button"
              role="tab"
              aria-selected={view === "roster"}
              className={`staff-mobile__tab ${view === "roster" ? "staff-mobile__tab--active" : ""}`}
              data-testid="staff-mobile-tab-roster"
              onClick={() => setView("roster")}
            >
              Roster
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "inbox"}
              className={`staff-mobile__tab ${view === "inbox" ? "staff-mobile__tab--active" : ""}`}
              data-testid="staff-mobile-tab-inbox"
              onClick={() => setView("inbox")}
            >
              Waiting on you
            </button>
          </div>

          {view === "inbox" ? (
            <div className="staff-mobile__content" data-testid="staff-mobile-inbox-view">
              <InboxPanel />
            </div>
          ) : (
            <div className="staff-mobile__content">
              <div className="staff-mobile__search-container">
                <input
                  ref={searchInputRef}
                  type="search"
                  className="staff-mobile__search-input"
                  placeholder="Search staff roles…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  aria-label="Search staff roles"
                />
              </div>

              <div
                className="staff-mobile__ask-barb"
                data-testid="staff-mobile-ask-barb"
                role="button"
                tabIndex={0}
                onClick={() => handleSelectRole("barb")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSelectRole("barb");
                  }
                }}
              >
                <div className="staff-mobile__ask-barb-icon">🤖</div>
                <div className="staff-mobile__ask-barb-text">
                  <div className="staff-mobile__ask-barb-title">Ask Barb (auto-route)</div>
                  <div className="staff-mobile__ask-barb-subtitle">
                    Ask anything — Barb routes to the right specialist
                  </div>
                </div>
              </div>

              <div className="staff-mobile__roles-list">
                {ROSTER_GROUPS.map((group) => {
                  const groupRoles = filteredRoles.filter((r) => r.group === group.key);
                  if (groupRoles.length === 0) return null;
                  return (
                    <div key={group.key} className="staff-mobile__role-group">
                      <div className="staff-mobile__role-group-title">
                        {group.label} ({groupRoles.length})
                      </div>
                      <div className="staff-mobile__role-group-items">
                        {groupRoles.map((role) => (
                          <div
                            key={role.name}
                            data-testid={`staff-mobile-role-${role.name}`}
                            role="button"
                            tabIndex={0}
                            onClick={() => handleSelectRole(role.name)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                handleSelectRole(role.name);
                              }
                            }}
                            className="staff-mobile__role-item"
                          >
                            <div style={{ minWidth: 0, flex: 1 }}>
                              <div className="staff-mobile__role-title">{role.title}</div>
                              {role.summary && (
                                <div className="staff-mobile__role-summary">{role.summary}</div>
                              )}
                            </div>
                            {role.caller_unread_count ? (
                              <span className="staff-mobile__unread-badge">
                                {role.caller_unread_count}
                              </span>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default StaffConsoleMobile;
