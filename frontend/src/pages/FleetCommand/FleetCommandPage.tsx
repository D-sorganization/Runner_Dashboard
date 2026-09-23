/**
 * FleetCommandPage.tsx — the "Fleet Command" tab (issue #1233, epic #1192).
 *
 * One place to coordinate fleet development: what the board and the operator
 * say matters (Priorities + Directives), who is working on what (Active
 * work), the board mailbox (Messages), issue claims (Claims) and one-click
 * staff dispatch (Dispatch). Backed by `/api/priorities` (#1227),
 * `/api/coordination` (#1229) and `/api/staff` (#1198).
 *
 * Orthogonality: every panel loads its own endpoint and degrades on its own —
 * a node without the coordination API still shows priorities, and vice versa.
 */
import { useCallback, useState } from "react";
import { SubTabs } from "../../components/SubTabs";
import { ActiveWorkPanel } from "./ActiveWorkPanel";
import { ClaimsPanel } from "./ClaimsPanel";
import { DirectivesPanel } from "./DirectivesPanel";
import { DispatchPanel } from "./DispatchPanel";
import { MessagesPanel, type MessageTarget } from "./MessagesPanel";
import { PrioritiesPanel } from "./PrioritiesPanel";

export type FleetCommandSection = "priorities" | "work" | "messages" | "claims" | "dispatch";

const SECTION_TABS: { key: FleetCommandSection; label: string }[] = [
  { key: "priorities", label: "Priorities" },
  { key: "work", label: "Active work" },
  { key: "messages", label: "Messages" },
  { key: "claims", label: "Claims" },
  { key: "dispatch", label: "Dispatch" },
];

export function FleetCommandPage() {
  const [section, setSection] = useState<FleetCommandSection>("priorities");
  const [messageTarget, setMessageTarget] = useState<MessageTarget | null>(null);

  const messageSession = useCallback((session: string, repo: string) => {
    setMessageTarget({ session, repo });
    setSection("messages");
  }, []);

  return (
    <div className="staff fleet-cmd">
      <header className="fleet-cmd__header">
        <h2 className="fleet-cmd__title">Fleet Command</h2>
        <p className="staff-muted">
          Board priorities, operator directives, who is on what, messages, claims and dispatch.
        </p>
      </header>
      <SubTabs
        tabs={SECTION_TABS}
        activeKey={section}
        onChange={(key) => setSection(key as FleetCommandSection)}
        ariaLabel="Fleet Command sections"
        className="staff__tabs"
      />
      {section === "priorities" ? (
        <div className="fleet-cmd__stack">
          <PrioritiesPanel />
          <DirectivesPanel />
        </div>
      ) : null}
      {section === "work" ? <ActiveWorkPanel onMessage={messageSession} /> : null}
      {section === "messages" ? <MessagesPanel target={messageTarget} /> : null}
      {section === "claims" ? <ClaimsPanel /> : null}
      {section === "dispatch" ? <DispatchPanel /> : null}
    </div>
  );
}

export default FleetCommandPage;
