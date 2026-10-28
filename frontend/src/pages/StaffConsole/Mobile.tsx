/** Mobile Staff Console: roster, threads, composer (SC-D8). */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { StaffRoleItem } from "./types";
import { ROSTER_GROUPS } from "./types";
import { categorizeRole } from "./rosterUtils";
import type { ProposalApproveHandler, ProposalDenyHandler } from "./cards/cardTypes";
import type { SendMessagePayload, ThreadInfo, ThreadMessage } from "./threadTypes";
import { Thread } from "./Thread";
import { Composer } from "./Composer";
import { GroupCostConfirm } from "./GroupCostConfirm";
import { MobileContextDrawer } from "./MobileContextDrawer";
import { ConsoleErrorBanner } from "./ConsoleErrorBanner";
import { InboxPanel } from "../Staff/InboxPanel";
import { threadKindForRole } from "./consoleThreads";
import { isPanelThread } from "./panelTurn";
import { useStaffConsole } from "./useStaffConsole";
import { BotIcon } from "../../shell/navIcons";
import { MobileRunsView } from "./MobileRuns";
import { PROJECT_WORKFLOWS } from "./projectWorkflows";
import { selectMobileThread, startNewMobileConversation, useMobileHomeSendNavigation } from "./useMobileHomeSendNavigation";
import "./mobile.css";

export type MobileView = "home" | "roster" | "thread" | "inbox" | "runs";

export interface StaffConsoleMobileProps {
  roles?: StaffRoleItem[];
  initialView?: MobileView;
  initialRole?: string;
  initialThread?: ThreadInfo;
  initialMessages?: ThreadMessage[];
  onOpenThread?: (threadId: string) => void;
  onThreadSelected?: (threadId: string, role: string) => void;
  onOpenRun?: (runId: string) => void;
  onSendMessage?: (payload: SendMessagePayload) => Promise<{ ok: boolean; [key: string]: unknown }>;
  onApproveProposal?: ProposalApproveHandler;
  onDenyProposal?: ProposalDenyHandler;
  className?: string;
}

function parseUrlParams(): { threadId: string | null; roleName: string | null; tab: string | null } {
  if (typeof window === "undefined") return { threadId: null, roleName: null, tab: null };
  const p = new URLSearchParams(window.location.search);
  const tabParam = p.get("tab") || (p.get("inbox") ? "inbox" : p.get("runs") ? "runs" : null);
  return {
    threadId: p.get("thread"),
    roleName: p.get("role"),
    tab: tabParam,
  };
}

export const StaffConsoleMobile: React.FC<StaffConsoleMobileProps> = ({
  roles: initialRoles,
  initialView = "roster",
  initialRole,
  initialThread,
  initialMessages,
  onOpenThread,
  onThreadSelected,
  onOpenRun,
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
  const { roles, selectedRole, activeThread, currentRole: currentRoleObj, sendMessage } = sc;
  const [view, setView] = useState<MobileView>(initialView);
  const viewRef = useRef(view);
  viewRef.current = view;

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
  const [composerPrefill, setComposerPrefill] = useState("");
  const [prefillSeq, setPrefillSeq] = useState(0);
  const applyComposerPrefill = (nextText: string) => { setComposerPrefill(nextText); setPrefillSeq((n) => n + 1); };
  const revealThreadAfterHomeSend = useMobileHomeSendNavigation({
    activeThread,
    isHomeView: () => viewRef.current === "home",
    onOpenThread,
    setThreadView: () => setView("thread"),
  });

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
    else if (tab === "runs") setView("runs");
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
    async (roleName: string, prefill = "") => {
      const thread = await sc.openRole(roleName);
      if (!thread) return;
      applyComposerPrefill(prefill);
      setView("thread");
      selectMobileThread(thread.id, roleName, onThreadSelected, onOpenThread);
    },
    [sc, onOpenThread, onThreadSelected],
  );

  // Return to roster view
  const handleBackToRoster = useCallback(() => {
    setView("roster");
    setShowContext(false);
    if (typeof window !== "undefined") {
      window.history.pushState(null, "", window.location.pathname);
    }
  }, []);

  const handleSendMessage = useCallback(async (payload: SendMessagePayload) => {
    const result = await sendMessage(payload);
    if (result.ok !== false && viewRef.current === "home" && !onSendMessage) {
      revealThreadAfterHomeSend();
    }
    return result;
  }, [onSendMessage, revealThreadAfterHomeSend, sendMessage]);
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
          className="staff-mobile__content staff-mobile__thread-content"
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
                <svg
                  className="staff-mobile__back-icon"
                  width="16"
                  height="16"
                  viewBox="0 0 16 16"
                  fill="none"
                  aria-hidden="true"
                >
                  <path
                    d="M10 3.5 5.5 8l4.5 4.5"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                <span>Roster</span>
              </button>
              <h1 className="staff-mobile__title" ref={threadHeadingRef} tabIndex={-1}>
                {activeThread.title}
              </h1>
            </div>
            <div className="staff-mobile__header-right">
              <button
                type="button"
                className="staff-mobile__details-btn"
                data-testid="staff-mobile-new-conversation-btn"
                aria-label="New conversation"
                onClick={() => void startNewMobileConversation(sc.newConversation, currentRoleObj.name, onThreadSelected, onOpenThread)}
              >
                New conversation
              </button>
              <button
                type="button"
                className="staff-mobile__details-btn"
                data-testid="staff-mobile-details-btn"
                aria-label="Role Details"
                onClick={() => setShowContext(true)}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="16" x2="12" y2="12" />
                  <line x1="12" y1="8" x2="12.01" y2="8" />
                </svg>
              </button>
            </div>
          </header>

          <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />

          <div className="staff-mobile__message-list" style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column" }}>
            {displayMessages.length === 0 ? (
              <div className="staff-mobile__thread-empty" data-testid="staff-mobile-thread-empty">
                <p className="staff-mobile__thread-empty-title">No messages yet</p>
                <p className="staff-mobile__thread-empty-hint">
                  Send a message below to start the conversation.
                </p>
              </div>
            ) : (
              <Thread
                thread={activeThread}
                messages={displayMessages}
                roles={roles}
                onApproveProposal={handleApproveProposal}
                onDenyProposal={handleDenyProposal}
                onCancelRun={sc.cancelRun}
                onAnswerRun={sc.answerRun}
                onFollowHandoff={(role) => void sc.openRole(role)}
              />
            )}
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
              placeholder={`Message ${currentRoleObj.title || "staff"}…`} isPanel={isPanelThread(activeThread)} prefilledText={composerPrefill} prefillSeq={prefillSeq}
            />
          </div>

          {showContext && (
            <MobileContextDrawer
              role={currentRoleObj}
              threadId={activeThread?.id}
              onClose={() => setShowContext(false)}
            />
          )}
        </div>
      )}

      {/* ── Chat-first home ────────────────────────────────────────────────── */}
      {view === "home" && (
        <div className="staff-mobile__view-container" data-testid="staff-mobile-home" role="region" aria-label="Ask Barb">
          <header className="staff-mobile__header">
            <h1 className="staff-mobile__title">Ask Barb</h1>
            <button type="button" className="staff-mobile__details-btn" onClick={() => setView("roster")}>Choose staff</button>
          </header>
          <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />
          <main className="staff-mobile__main-content staff-mobile__home-content">
            <section className="staff-mobile__home-intro" aria-labelledby="staff-mobile-home-title">
              <span className="staff-mobile__home-eyebrow">Your fleet, one conversation away</span>
              <h2 id="staff-mobile-home-title">What would you like to work on?</h2>
              <p>Ask Barb to help, or start with a project workflow. Nothing runs until you send.</p>
            </section>
            <div className="staff-mobile__project-starters" aria-label="Project starters">
              {PROJECT_WORKFLOWS.map(({ project, prompt }) => (
                <button key={project} type="button" className="staff-mobile__project-starter" aria-label={`Start ${project}`} onClick={() => applyComposerPrefill(prompt)}>
                  <span>Start {project}</span><span>Prepare a test-first triage</span>
                </button>
              ))}
            </div>
          </main>
          <div className="staff-mobile__composer-container" data-testid="staff-mobile-composer-container">
            <GroupCostConfirm guard={sc.costGuard} />
            <Composer threadId="new" roles={roles} selectedRole="barb" onSendMessage={handleSendMessage} placeholder="Message Barb..." prefilledText={composerPrefill} prefillSeq={prefillSeq} />
          </div>
          <nav className="staff-mobile__tabs" role="tablist" aria-label="Staff views">
            <button type="button" role="tab" aria-selected={false} className="staff-mobile__tab" data-testid="staff-mobile-tab-roster" onClick={() => setView("roster")}>Choose staff</button>
            <button type="button" role="tab" aria-selected={false} className="staff-mobile__tab" data-testid="staff-mobile-tab-inbox" onClick={() => setView("inbox")}>Inbox</button>
            <button type="button" role="tab" aria-selected={false} className="staff-mobile__tab" data-testid="staff-mobile-tab-runs" onClick={() => setView("runs")}>Runs</button>
          </nav>
        </div>
      )}

      {/* ── Roster, Inbox & Runs Views ────────────────────────────────────── */}
      {view !== "thread" && view !== "home" && (
        <div
          className="staff-mobile__view-container"
          data-testid="staff-mobile-roster"
          role="region"
          aria-label="Staff Console"
        >
          <header className="staff-mobile__header">
            <h1 className="staff-mobile__title">Staff Console</h1>
          </header>

          <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />

          <div className="staff-mobile__main-content">
            {view === "inbox" ? (
              <div className="staff-mobile__content" data-testid="staff-mobile-inbox-view">
                <InboxPanel />
              </div>
            ) : view === "runs" ? (
              <MobileRunsView roles={roles} onOpenRun={onOpenRun} />
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
                  <div className="staff-mobile__ask-barb-icon">
                    <BotIcon className="staff-mobile__bot-glyph" />
                  </div>
                  <div className="staff-mobile__ask-barb-text">
                    <div className="staff-mobile__ask-barb-title">Ask Barb (auto-route)</div>
                    <div className="staff-mobile__ask-barb-subtitle">
                      Ask anything — Barb routes to the right specialist
                    </div>
                  </div>
                </div>

                <div className="staff-mobile__project-starters" aria-label="Project starters">
                  {PROJECT_WORKFLOWS.map(({ project, prompt }) => (
                    <button key={project} type="button" className="staff-mobile__project-starter" aria-label={`Start ${project}`} onClick={() => void handleSelectRole("barb", prompt)}>
                      <span>Start {project}</span>
                      <span>Prepare a test-first triage</span>
                    </button>
                  ))}
                </div>

                <div className="staff-mobile__roles-list">
                  {ROSTER_GROUPS.map((group) => {
                    // Same tiering as the desktop roster: the API's free-form `group` is normalised.
                    const groupRoles = filteredRoles.filter((r) => categorizeRole(r) === group.key);
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
                              title={`Open a conversation with ${role.title}`}
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
                                <span
                                  className="staff-mobile__unread-badge"
                                  title={`${role.caller_unread_count} unread message${role.caller_unread_count === 1 ? "" : "s"}`}
                                >
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

          <nav className="staff-mobile__tabs" role="tablist" aria-label="Staff views">
            <button
              type="button"
              role="tab"
              aria-selected={view === "roster"}
              className={`staff-mobile__tab ${view === "roster" ? "staff-mobile__tab--active" : ""}`}
              data-testid="staff-mobile-tab-roster"
              onClick={() => setView("roster")}
            >
              Console
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "inbox"}
              className={`staff-mobile__tab ${view === "inbox" ? "staff-mobile__tab--active" : ""}`}
              data-testid="staff-mobile-tab-inbox"
              onClick={() => setView("inbox")}
            >
              Inbox
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "runs"}
              className={`staff-mobile__tab ${view === "runs" ? "staff-mobile__tab--active" : ""}`}
              data-testid="staff-mobile-tab-runs"
              onClick={() => setView("runs")}
            >
              Runs
            </button>
          </nav>
        </div>
      )}
    </div>
  );
};

export default StaffConsoleMobile;
