/**
 * ProposalsPanel.tsx — Board Proposals suggestion box (#1284, CR-7).
 *
 * Provides:
 * 1. A proposal submission form with validation and duplicate-candidate detection.
 * 2. An open proposals list showing in-flight suggestions before the Board.
 * 3. A decided proposals list showing Board outcomes with badges and meeting links.
 */
import { fetchProposals, useResource } from "./fleetApi";
import { ProposalForm } from "./ProposalForm";
import { DecidedProposalsSection, OpenProposalsSection } from "./ProposalLists";
import type { CreateProposalPayload } from "./types";

interface ProposalsPanelProps {
  initialPrefill?: Partial<CreateProposalPayload>;
}

export function ProposalsPanel({ initialPrefill }: ProposalsPanelProps) {
  const openRes = useResource(
    (signal) => fetchProposals({ state: "open" }, signal),
    "proposals:open",
  );
  const decidedRes = useResource(
    (signal) => fetchProposals({ state: "decided" }, signal),
    "proposals:decided",
  );

  const handleSuccess = () => {
    openRes.reload();
    decidedRes.reload();
  };

  const openList = openRes.data?.proposals ?? [];
  const decidedList = decidedRes.data?.proposals ?? [];

  return (
    <div className="fleet-cmd__stack">
      {/* ── Submission Form ── */}
      <ProposalForm
        initialPrefill={initialPrefill}
        onSuccess={handleSuccess}
      />

      {/* ── Open Proposals ── */}
      <OpenProposalsSection
        proposals={openList}
        loading={openRes.loading}
        hasData={openRes.data !== null}
        error={openRes.error}
        onRetry={openRes.reload}
      />

      {/* ── Decided Proposals ── */}
      <DecidedProposalsSection
        proposals={decidedList}
        loading={decidedRes.loading}
        hasData={decidedRes.data !== null}
        error={decidedRes.error}
        onRetry={decidedRes.reload}
      />
    </div>
  );
}

export default ProposalsPanel;
