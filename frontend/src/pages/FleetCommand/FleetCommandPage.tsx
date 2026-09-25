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
import { ProposalsPanel } from "./ProposalsPanel";
import type { CreateProposalPayload } from "./types";

export type FleetCommandSection = "priorities" | "proposals" | "work" | "messages" | "claims" | "dispatch";

const SECTION_TABS: { key: FleetCommandSection; label: string }[] = [
  { key: "priorities", label: "Priorities" },
  { key: "proposals", label: "Proposals" },
  { key: "work", label: "Active work" },
  { key: "messages", label: "Messages" },
  { key: "claims", label: "Claims" },
  { key: "dispatch", label: "Dispatch" },
];

function parseInitialState(): { section: FleetCommandSection; prefill: Partial<CreateProposalPayload> | null } {
  if (typeof window === "undefined") return { section: "priorities", prefill: null };
  const params = new URLSearchParams(window.location.search);
  const sec = params.get("section");
  const isSection = (s: string | null): s is FleetCommandSection =>
    s === "priorities" || s === "proposals" || s === "work" || s === "messages" || s === "claims" || s === "dispatch";

  let section: FleetCommandSection = isSection(sec) ? sec : "priorities";

  const title = params.get("title");
  const repo = params.get("repo") || params.get("target_repos");
  const problem = params.get("problem");
  const evidence = params.get("evidence");
  const options = params.get("options_considered");
  const lean = params.get("lean");
  const cost = params.get("estimated_cost");
  const urgency = params.get("urgency");
  const code_request_url = params.get("code_request_url");

  const isEffort = (v: string | null): v is CreateProposalPayload["estimated_cost"] =>
    v === "Low" || v === "Medium" || v === "High";
  const isUrgency = (v: string | null): v is CreateProposalPayload["urgency"] =>
    v === "Routine" || v === "Urgent" || v === "Emergency";

  let prefill: Partial<CreateProposalPayload> | null = null;
  if (title || repo || problem || code_request_url) {
    if (!sec) section = "proposals";
    prefill = {
      title: title || "",
      target_repos: repo ? [repo] : [],
      problem: problem || "",
      evidence: evidence || "",
      options_considered: options || "",
      lean: lean || "",
      estimated_cost: isEffort(cost) ? cost : "Medium",
      urgency: isUrgency(urgency) ? urgency : "Routine",
      code_request_url: code_request_url || "",
    };
  }

  return { section, prefill };
}

export function FleetCommandPage() {
  // Lazy initializer: parseInitialState() reads window.location, which
  // should run once on mount, not be recomputed on every render.
  const [initial] = useState(() => parseInitialState());
  const [section, setSection] = useState<FleetCommandSection>(initial.section);
  // `proposalPrefill` is set once from the initial URL and never updated
  // afterwards — no setter needed.
  const [proposalPrefill] = useState<Partial<CreateProposalPayload> | null>(initial.prefill);
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
          Board priorities, operator directives, proposals, who is on what, messages, claims and dispatch.
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
      {section === "proposals" ? <ProposalsPanel initialPrefill={proposalPrefill ?? undefined} /> : null}
      {section === "work" ? <ActiveWorkPanel onMessage={messageSession} /> : null}
      {section === "messages" ? <MessagesPanel target={messageTarget} /> : null}
      {section === "claims" ? <ClaimsPanel /> : null}
      {section === "dispatch" ? <DispatchPanel /> : null}
    </div>
  );
}

export default FleetCommandPage;
