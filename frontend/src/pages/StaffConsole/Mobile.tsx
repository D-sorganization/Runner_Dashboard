/**
 * Mobile.tsx — Mobile Staff Console (SC-D8, Issue #1331).
 *
 * Implements full-screen roster → thread navigation, safe-area aware bottom
 * composer, thumb-reachable approve/deny actions, push deep linking (?thread=, ?role=),
 * and role context inspection.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { StaffRoleItem } from "./types";
import { ROSTER_GROUPS } from "./types";
import type { SendMessagePayload, ThreadInfo, ThreadMessage } from "./threadTypes";
import type { RoleDetail } from "./contextTypes";
import { Thread } from "./Thread";
import { Composer } from "./Composer";
import { ContextPane } from "./ContextPane";
import { InboxPanel } from "../Staff/InboxPanel";
import {
  fetchRoster,
  fetchThreadMessages,
  postThreadMessage,
  decideActionProposal,
} from "../Staff/staffApi";
import { useThreadStream } from "./useThreadStream";
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
  onApproveProposal?: (proposalId: string, params?: Record<string, unknown>) => void;
  onDenyProposal?: (proposalId: string) => void;
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
  initialMessages = [],
  onOpenThread,
  onSendMessage: externalSendMessage,
  onApproveProposal: externalApproveProposal,
  onDenyProposal: externalDenyProposal,
  className = "",
}) => {
  const [roles, setRoles] = useState<StaffRoleItem[]>(initialRoles || []);
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
  const [selectedRole, setSelectedRole] = useState<string | null>(initialRole || null);
  const [activeThread, setActiveThread] = useState<ThreadInfo | null>(initialThread || null);
  const [messages, setMessages] = useState<ThreadMessage[]>(initialMessages);
  const [searchQuery, setSearchQuery] = useState("");
  const [showContext, setShowContext] = useState(false);

  // Sync roles prop if provided
  useEffect(() => {
    if (initialRoles && initialRoles.length > 0) {
      setRoles(initialRoles);
    } else {
      fetchRoster().then((res) => {
        if (res?.roles) setRoles(res.roles as StaffRoleItem[]);
      }).catch(() => {});
    }
  }, [initialRoles]);

  // Deep Link Handling (?thread=, ?role=, ?tab=)
  const syncWithUrl = useCallback(() => {
    const { threadId, roleName, tab } = parseUrlParams();
    if (threadId) {
      setView("thread");
      setActiveThread((prev) => prev?.id === threadId ? prev : {
        id: threadId,
        title: roleName ? `Conversation with ${roleName}` : "Conversation",
        kind: "direct",
        participants: ["user", roleName || "staff"],
        status: "active",
      });
      if (roleName) setSelectedRole(roleName);
      return;
    }
    if (roleName) {
      setView("thread");
      setSelectedRole(roleName);
      setActiveThread((prev) => prev?.participants.includes(roleName) ? prev : {
        id: `thread-${roleName}-direct`,
        title: roleName === "barb" ? "Conversation with Barb" : roleName,
        kind: roleName === "barb" ? "auto" : "direct",
        participants: ["user", roleName],
        status: "active",
      });
      return;
    }
    if (tab === "inbox") setView("inbox");
  }, []);

  useEffect(() => {
    syncWithUrl();
    window.addEventListener("popstate", syncWithUrl);
    return () => window.removeEventListener("popstate", syncWithUrl);
  }, [syncWithUrl]);

  // Load thread messages if needed
  useEffect(() => {
    if (activeThread?.id && initialMessages.length === 0) {
      fetchThreadMessages(activeThread.id).then((res) => {
        if (res?.messages) setMessages(res.messages);
      }).catch(() => {});
    }
  }, [activeThread?.id, initialMessages.length]);

  // SSE streaming integration
  const { messages: streamedMessages } = useThreadStream({
    threadId: activeThread?.id || "",
    initialMessages: messages,
    enabled: Boolean(activeThread?.id && view === "thread"),
  });

  const displayMessages = streamedMessages.length > 0 ? streamedMessages : messages;

  // Open a specific role conversation
  const handleSelectRole = useCallback(
    (roleName: string) => {
      setSelectedRole(roleName);
      const isBarb = roleName.toLowerCase() === "barb";
      const newThread: ThreadInfo = {
        id: isBarb ? "thread-barb-auto" : `thread-${roleName}-direct`,
        title: isBarb ? "Conversation with Barb" : (roles.find((r) => r.name === roleName)?.title || roleName),
        kind: isBarb ? "auto" : "direct",
        participants: ["user", roleName],
        status: "active",
      };
      setActiveThread(newThread);
      setView("thread");
      onOpenThread?.(newThread.id);

      if (typeof window !== "undefined") {
        const nextUrl = `?role=${encodeURIComponent(roleName)}&thread=${encodeURIComponent(newThread.id)}`;
        window.history.pushState(null, "", nextUrl);
      }
    },
    [roles, onOpenThread],
  );

  // Return to roster view
  const handleBackToRoster = useCallback(() => {
    setView("roster");
    setShowContext(false);
    if (typeof window !== "undefined") {
      window.history.pushState(null, "", window.location.pathname);
    }
  }, []);

  // Send message handler
  const handleSendMessage = useCallback(
    async (payload: SendMessagePayload) => {
      if (externalSendMessage) return externalSendMessage(payload);
      if (!activeThread?.id) return { ok: false, error: "No active thread" };
      try {
        const res = await postThreadMessage(
          activeThread.id,
          { body_md: payload.body, author: "user", author_kind: "user", meta: payload.meta },
          payload.idempotencyKey,
        );
        return { ok: true, message: res };
      } catch (err: unknown) {
        return { ok: false, error: err instanceof Error ? err.message : String(err) };
      }
    },
    [activeThread?.id, externalSendMessage],
  );

  // Approval actions
  const handleApproveProposal = useCallback(
    (proposalId: string, params?: Record<string, unknown>) => {
      if (externalApproveProposal) {
        externalApproveProposal(proposalId, params);
        return;
      }
      decideActionProposal(proposalId, "approved", "Approved via Mobile Console").catch(() => {});
    },
    [externalApproveProposal],
  );

  const handleDenyProposal = useCallback(
    (proposalId: string) => {
      if (externalDenyProposal) {
        externalDenyProposal(proposalId);
        return;
      }
      decideActionProposal(proposalId, "denied", "Denied via Mobile Console").catch(() => {});
    },
    [externalDenyProposal],
  );

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

  // Find Barb role
  const barbRole = useMemo(() => roles.find((r) => r.name === "barb") || {
    name: "barb", title: "Barb", summary: "Fleet Orchestrator and conversational concierge",
    group: "leadership", valid: true,
  }, [roles]);

  // Current active role object
  const currentRoleObj = useMemo(
    () => roles.find((r) => r.name === selectedRole) || barbRole,
    [roles, selectedRole, barbRole],
  );

  // Formatted RoleDetail for ContextPane
  const roleDetail: RoleDetail = useMemo(() => ({
    name: currentRoleObj.name,
    title: currentRoleObj.title,
    mandate: currentRoleObj.summary || "",
    providers: Array.isArray(currentRoleObj.providers)
      ? currentRoleObj.providers.map((p) => typeof p === "string" ? { name: p, signed_in: true } : p)
      : [],
    budget: currentRoleObj.budget ? {
      usd_per_day: currentRoleObj.budget.daily_limit ?? 50,
      usd_today: currentRoleObj.budget.spend_today ?? 0,
    } : undefined,
    active_runs: [],
    recent_work_items: [],
  }), [currentRoleObj]);

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
