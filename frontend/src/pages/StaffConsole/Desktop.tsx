/**
 * Desktop.tsx — three-pane desktop Staff Console (#1446, UX spec
 * docs/design/staff-console.md §5): Roster | Thread + Composer | Context pane.
 *
 * State comes from `useStaffConsole`, shared with the mobile layout. Nothing
 * is fetched or created until the user picks a role.
 */
import { useState } from "react";
import { Composer } from "./Composer";
import { ConsoleErrorBanner } from "./ConsoleErrorBanner";
import { ContextPane } from "./ContextPane";
import type { ThreadApi } from "./consoleThreads";
import { Roster } from "./Roster";
import { Thread } from "./Thread";
import type { StaffRoleItem } from "./types";
import { useStaffConsole } from "./useStaffConsole";
import "./desktop.css";

export interface StaffConsoleDesktopProps {
  /** Seed roster (tests); the console loads `/api/v1/staff/roster` when absent. */
  roles?: StaffRoleItem[];
  threadApi?: ThreadApi;
}

export function StaffConsoleDesktop({ roles: seedRoles, threadApi }: StaffConsoleDesktopProps) {
  const sc = useStaffConsole({ roles: seedRoles, threadApi });
  const [showContext, setShowContext] = useState(true);
  const { roles, activeThread, currentRole } = sc;
  const rosterError = sc.error?.kind === "roster" ? sc.error.message : null;

  return (
    <div className={`staff-console${showContext ? "" : " staff-console--no-context"}`} data-testid="staff-console-desktop">
      <Roster
        className="staff-console__roster"
        roles={roles}
        selectedRoleId={sc.selectedRole ?? undefined}
        onSelectRole={(name) => void sc.openRole(name)}
        isLoading={sc.rosterLoading}
        isError={Boolean(rosterError)}
        errorMessage={rosterError ?? undefined}
      />

      <section className="staff-console__main" role="region" aria-label="Staff conversation">
        <header className="staff-console__header">
          <h2 className="staff-console__title">{activeThread ? activeThread.title : "Staff Console"}</h2>
          <button
            type="button"
            className="staff-console__context-toggle"
            aria-expanded={showContext}
            onClick={() => setShowContext((shown) => !shown)}
          >
            {showContext ? "Hide role context" : "Show role context"}
          </button>
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
                onApproveProposal={(id, params) => void sc.approveProposal(id, params)}
                onDenyProposal={(id) => void sc.denyProposal(id)}
              />
            </div>
            <Composer
              threadId={activeThread.id}
              roles={roles}
              selectedRole={sc.selectedRole ?? undefined}
              onSendMessage={sc.sendMessage}
              placeholder={`Message ${currentRole.title || "staff"}…`}
              focusOnThreadChange
            />
          </>
        ) : (
          <p className="staff-console__empty">
            {sc.openingRole ? `Opening conversation with ${sc.openingRole}…` : "Pick a role or ask Barb to start a conversation."}
          </p>
        )}
      </section>

      {showContext && (
        <aside className="staff-console__context" aria-label="Role context">
          <ContextPane role={sc.roleDetail} />
        </aside>
      )}
    </div>
  );
}

export default StaffConsoleDesktop;
