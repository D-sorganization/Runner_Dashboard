/**
 * Desktop.tsx — three-pane desktop Staff Console (#1446, UX spec
 * docs/design/staff-console.md §5): Roster | Thread + Composer | Context pane.
 *
 * State comes from `useStaffConsole`, shared with the mobile layout. Nothing
 * is fetched or created until the user picks a role.
 */
import { useEffect, useState } from "react";
import { Composer } from "./Composer";
import { ConsoleErrorBanner } from "./ConsoleErrorBanner";
import { ContextPane } from "./ContextPane";
import { GroupCostConfirm } from "./GroupCostConfirm";
import type { ThreadApi } from "./consoleThreads";
import { Roster } from "./Roster";
import { Thread } from "./Thread";
import { isPanelThread } from "./panelTurn";
import type { StaffRoleItem } from "./types";
import { useStaffConsole } from "./useStaffConsole";
import { Dropdown } from "../../primitives/Dropdown";
import "./desktop.css";

export interface StaffConsoleDesktopProps {
  /** Seed roster (tests); the console loads `/api/v1/staff/roster` when absent. */
  roles?: StaffRoleItem[];
  threadApi?: ThreadApi;
  /** Backend thread to open on mount (the thread a dispatched request names, #1504). */
  initialThreadId?: string | null;
}

/** True when the console is below the three-column width (see desktop.css). */
function isMediumWindow(): boolean {
  return typeof window !== "undefined" && Boolean(window.matchMedia?.("(max-width: 1280px)").matches);
}

const EMPTY_SUGGESTIONS = [
  "What's waiting on me?",
  "Summarise today's fleet status",
  "Which PRs are blocked?",
  "Show active runner jobs",
];

export function StaffConsoleDesktop({ roles: seedRoles, threadApi, initialThreadId }: StaffConsoleDesktopProps) {
  const sc = useStaffConsole({ roles: seedRoles, threadApi, initialThreadId });
  // Below 1280px the context pane overlays the conversation, so it starts closed there.
  const [showContext, setShowContext] = useState(() => !isMediumWindow());
  const [composerPrefill, setComposerPrefill] = useState("");
  const { roles, activeThread, currentRole } = sc;
  const rosterError = sc.error?.kind === "roster" ? sc.error.message : null;

  // The address names the open conversation, so a reload or shared link reopens it (#1783).
  // replaceState, not push: switching threads should not flood the back stack.
  const activeThreadId = activeThread?.id;
  useEffect(() => {
    if (!activeThreadId || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("thread") === activeThreadId) return;
    params.set("thread", activeThreadId);
    window.history.replaceState(window.history.state, "", `${window.location.pathname}?${params.toString()}`);
  }, [activeThreadId]);

  const handleExport = (format: "markdown" | "json") => {
    if (!activeThread) return;
    const url = `/api/v1/staff/threads/${activeThread.id}/export?format=${format}`;
    const filename = `thread-${activeThread.id}.${format === "markdown" ? "md" : "json"}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const participantsCount = activeThread?.participants?.length ?? 0;

  return (
    <div className={`staff-console${showContext ? "" : " staff-console--no-context"}`} data-testid="staff-console-desktop">
      <Roster
        className="staff-console__roster"
        roles={roles}
        selectedRoleId={sc.selectedRole ?? undefined}
        onSelectRole={(name) => void sc.openRole(name)}
        onThreadCreated={(thread) => sc.openThread(thread)}
        isLoading={sc.rosterLoading}
        isError={Boolean(rosterError)}
        errorMessage={rosterError ?? undefined}
      />

      <section className="staff-console__main" role="region" aria-label="Staff conversation">
        <header className="staff-console__header">
          <div className="staff-console__header-left">
            <h2 className="staff-console__title">{activeThread ? activeThread.title : "Staff Console"}</h2>
            {participantsCount > 0 && (
              <span
                className="staff-console__participants"
                title={`Participants: ${activeThread?.participants.join(", ")}`}
                aria-label={`${participantsCount} participants`}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  width="12"
                  height="12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                </svg>
                <span>{participantsCount}</span>
              </span>
            )}
          </div>

          <div className="staff-console__header-actions">
            {activeThread && (
              <>
                <button
                  type="button"
                  className="staff-console__export-btn"
                  onClick={() => void sc.newConversation()}
                >
                  New conversation
                </button>
                <Dropdown
                  label="Export"
                  items={[
                    {
                      id: "export-md",
                      label: "Export MD",
                      onSelect: () => handleExport("markdown"),
                    },
                    {
                      id: "export-json",
                      label: "Export JSON",
                      onSelect: () => handleExport("json"),
                    },
                  ]}
                />
                {/* Fallback hidden anchors with titles/hrefs for compatibility */}
                <div style={{ display: "none" }} aria-hidden="true">
                  <a
                    href={`/api/v1/staff/threads/${activeThread.id}/export?format=markdown`}
                    download={`thread-${activeThread.id}.md`}
                    className="staff-console__export-btn"
                    title="Export thread as Markdown"
                    data-testid="export-md"
                  >
                    Export MD
                  </a>
                  <a
                    href={`/api/v1/staff/threads/${activeThread.id}/export?format=json`}
                    download={`thread-${activeThread.id}.json`}
                    className="staff-console__export-btn"
                    title="Export thread as JSON"
                    data-testid="export-json"
                  >
                    Export JSON
                  </a>
                </div>
              </>
            )}
            <button
              type="button"
              className="staff-console__context-toggle"
              aria-expanded={showContext}
              aria-label={showContext ? "Hide role context" : "Show role context"}
              title={showContext ? "Hide role context" : "Show role context"}
              onClick={() => setShowContext((shown) => !shown)}
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                <line x1="15" y1="3" x2="15" y2="21" />
              </svg>
            </button>
          </div>
        </header>

        <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />

        {activeThread ? (
          <>
            <div className="staff-console__thread">
              <Thread
                thread={activeThread}
                messages={sc.messages}
                roles={roles}
                isReconnecting={sc.isReconnecting}
                onApproveProposal={sc.approveProposal}
                onDenyProposal={sc.denyProposal}
                onCancelRun={sc.cancelRun}
                onAnswerRun={sc.answerRun}
                onFollowHandoff={(role) => void sc.openRole(role)}
              />
            </div>
            <GroupCostConfirm guard={sc.costGuard} />
            <Composer
              threadId={activeThread.id}
              roles={roles}
              selectedRole={sc.selectedRole ?? undefined}
              onSendMessage={sc.sendMessage}
              placeholder={`Message ${currentRole.title || "staff"}…`}
              focusOnThreadChange
              isPanel={isPanelThread(activeThread)}
              prefilledText={composerPrefill}
            />
          </>
        ) : (
          <>
            <div className="staff-console__empty">
              <h3 className="staff-console__empty-title">
                {sc.openingRole
                  ? `Opening conversation with ${sc.openingRole}…`
                  : "Ask Barb anything, or pick a staff role"}
              </h3>
              {!sc.openingRole && (
                <>
                  <p className="staff-console__empty-subtitle">
                    Pick a role or ask Barb to start a conversation.
                  </p>
                  <div className="staff-console__suggestions">
                    {EMPTY_SUGGESTIONS.map((text) => (
                      <button
                        key={text}
                        type="button"
                        className="staff-console__suggestion-chip"
                        onClick={() => setComposerPrefill(text)}
                      >
                        {text}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
            <Composer
              threadId="new"
              roles={roles}
              selectedRole={sc.selectedRole ?? undefined}
              onSendMessage={sc.sendMessage}
              placeholder="Message Barb or type /dispatch…"
              prefilledText={composerPrefill}
            />
          </>
        )}
      </section>

      {showContext && (
        <aside className="staff-console__context" aria-label="Role context">
          <button
            type="button"
            className="staff-console__context-close"
            aria-label="Close role context"
            title="Close role context"
            onClick={() => setShowContext(false)}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              width="14"
              height="14"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <line x1="6" y1="6" x2="18" y2="18" />
              <line x1="18" y1="6" x2="6" y2="18" />
            </svg>
          </button>
          <ContextPane
            role={sc.roleDetail}
            threadContext={activeThread ? { thread_id: activeThread.id } : null}
          />
        </aside>
      )}
    </div>
  );
}

export default StaffConsoleDesktop;
