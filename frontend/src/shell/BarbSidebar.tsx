import React, { useEffect, useRef, useState } from "react";
import { Composer } from "../pages/StaffConsole/Composer";
import { ConsoleErrorBanner } from "../pages/StaffConsole/ConsoleErrorBanner";
import { GroupCostConfirm } from "../pages/StaffConsole/GroupCostConfirm";
import { Thread } from "../pages/StaffConsole/Thread";
import { useStaffConsole } from "../pages/StaffConsole/useStaffConsole";
import { ChatGlyph } from "../pages/decompIcons";
import "./BarbSidebar.css";

const QUICK_ROUTES = [
  { label: "Fleet Command", tabId: "fleet-command" },
  { label: "Projects", tabId: "projects" },
  { label: "Queue", tabId: "queue" },
];

const PROJECT_PROMPTS = [
  {
    repo: "UpstreamDrift",
    summary: "Review open work and identify the next useful action.",
    prompt:
      "In UpstreamDrift, review open issues and pull requests, summarize active work and recommend the most useful next step. Use the repository's registered workflows for any action you can take.",
  },
  {
    repo: "AffineDrift",
    summary: "Check project health and find a clear next step.",
    prompt:
      "In AffineDrift, review open issues and pull requests, summarize active work and recommend the most useful next step. Use the repository's registered workflows for any action you can take.",
  },
];

export interface BarbSidebarProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (tabId: string) => void;
  mobile?: boolean;
}

export function BarbSidebar({ open, onClose, onNavigate, mobile = false }: BarbSidebarProps): React.ReactElement {
  const sc = useStaffConsole({ initialRole: "barb" });
  const [composerPrefill, setComposerPrefill] = useState("");
  const sidebarRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    if (mobile && document.activeElement instanceof HTMLElement) {
      restoreFocusRef.current = document.activeElement;
    }
    closeButtonRef.current?.focus();
    const siblings = mobile && sidebarRef.current?.parentElement
      ? Array.from(sidebarRef.current.parentElement.children)
          .filter((element): element is HTMLElement => element instanceof HTMLElement && element !== sidebarRef.current)
          .map((element) => ({ element, wasInert: element.inert || element.hasAttribute("inert") }))
      : [];
    siblings.forEach(({ element }) => {
      element.inert = true;
      element.setAttribute("inert", "");
    });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      siblings.forEach(({ element, wasInert }) => {
        element.inert = wasInert;
        if (wasInert) element.setAttribute("inert", "");
        else element.removeAttribute("inert");
      });
      if (mobile && restoreFocusRef.current?.isConnected) {
        restoreFocusRef.current.focus();
      }
      restoreFocusRef.current = null;
    };
  }, [open, onClose, mobile]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (!mobile || !open || event.key !== "Tab") return;
    const panel = sidebarRef.current;
    if (!panel) return;
    const focusable = Array.from(panel.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
    if (focusable.length === 0) {
      event.preventDefault();
      panel.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <aside
      ref={sidebarRef}
      id="barb-sidebar"
      className={`barb-sidebar${open ? " barb-sidebar--open" : ""}${mobile ? " barb-sidebar--mobile" : ""}`}
      role={mobile ? "dialog" : undefined}
      aria-modal={mobile ? true : undefined}
      aria-label="Ask Barb"
      aria-hidden={!open}
      onKeyDown={handleKeyDown}
      data-testid="barb-sidebar"
    >
      <header className="barb-sidebar__header">
        <div className="barb-sidebar__identity">
          <span className="barb-sidebar__mark" aria-hidden="true">
            <ChatGlyph />
          </span>
          <div>
            <h2>Ask Barb</h2>
            <p>Fleet concierge</p>
          </div>
        </div>
        <div className="barb-sidebar__header-actions">
          {sc.activeThread && (
            <button
              type="button"
              className="barb-sidebar__new"
              onClick={() => void sc.newConversation()}
            >
              New chat
            </button>
          )}
          <button
            ref={closeButtonRef}
            type="button"
            className="barb-sidebar__close"
            aria-label="Close Ask Barb"
            onClick={onClose}
          >
            <span aria-hidden="true">&times;</span>
          </button>
        </div>
      </header>

      <nav className="barb-sidebar__routes" aria-label="Quick navigation">
        {QUICK_ROUTES.map((route) => (
          <button
            key={route.tabId}
            type="button"
            aria-label={`Open ${route.label}`}
            onClick={() => onNavigate(route.tabId)}
          >
            {route.label}
            <span aria-hidden="true">&rarr;</span>
          </button>
        ))}
      </nav>

      <div className="barb-sidebar__conversation">
        <ConsoleErrorBanner error={sc.error} onDismiss={sc.dismissError} />
        {sc.activeThread ? (
          <div className="barb-sidebar__thread">
            <Thread
              thread={sc.activeThread}
              messages={sc.messages}
              roles={sc.roles}
              isReconnecting={sc.isReconnecting}
              onApproveProposal={sc.approveProposal}
              onDenyProposal={sc.denyProposal}
              onCancelRun={sc.cancelRun}
              onAnswerRun={sc.answerRun}
              onFollowHandoff={(role) => void sc.openRole(role)}
            />
          </div>
        ) : (
          <div className="barb-sidebar__welcome">
            <span className="barb-sidebar__eyebrow">Your staff front door</span>
            <h3>What would you like to move forward?</h3>
            <p>Ask a question, choose a project, or jump to a fleet workspace.</p>
            <div className="barb-sidebar__projects" aria-label="Project starters">
              {PROJECT_PROMPTS.map((project) => (
                <button
                  key={project.repo}
                  type="button"
                  className="barb-sidebar__project"
                  aria-label={`Triage ${project.repo}`}
                  onClick={() => setComposerPrefill(project.prompt)}
                >
                  <span className="barb-sidebar__project-name">{project.repo}</span>
                  <span className="barb-sidebar__project-summary">{project.summary}</span>
                  <span className="barb-sidebar__project-action">Start with Barb &rarr;</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {sc.activeThread ? <GroupCostConfirm guard={sc.costGuard} /> : null}
        <Composer
          threadId={sc.activeThread?.id ?? "new"}
          roles={sc.roles}
          selectedRole="barb"
          onSendMessage={sc.sendMessage}
          placeholder="Message Barb..."
          focusOnThreadChange
          isPanel={false}
          prefilledText={composerPrefill}
        />
      </div>
    </aside>
  );
}
