import React, { useCallback } from "react";
import { useProposalAuthority } from "../useProposalAuthority";
import { ActionCard } from "./ActionCard";
import type {
  ActionProposalData,
  ProposalApproveHandler,
  ProposalDenyHandler,
} from "./cardTypes";

export interface ProposalActionCardProps {
  proposal: ActionProposalData;
  onApprove?: ProposalApproveHandler;
  onDeny?: ProposalDenyHandler;
}

/** The statuses for which ActionCard offers Approve and Deny (so a preview is useful). */
const DECIDED: ReadonlySet<string> = new Set(["approved", "denied", "executed", "expired"]);
const isUndecided = (proposal: ActionProposalData): boolean => !DECIDED.has(proposal.status);

/**
 * An ActionCard overlaid with the viewer's own approval preview (#1807). Only an undecided card is
 * previewed; a refused decision refreshes the preview so a stale hash or lost authority shows.
 */
export const ProposalActionCard: React.FC<ProposalActionCardProps> = ({
  proposal,
  onApprove,
  onDeny,
}) => {
  const { preview, loading, refresh } = useProposalAuthority(
    proposal.id,
    isUndecided(proposal),
  );

  const approve = useCallback<ProposalApproveHandler>(
    async (id, params, paramsHash) => {
      const outcome = await onApprove?.(id, params, paramsHash);
      if (outcome === false) refresh();
      return outcome;
    },
    [onApprove, refresh],
  );

  const merged: ActionProposalData = preview
    ? {
        ...proposal,
        can_approve: preview.can_approve,
        denial_reason: preview.denial_reason,
        params_hash: preview.params_hash,
      }
    : proposal;

  return (
    <ActionCard
      proposal={merged}
      onApprove={approve}
      onDeny={onDeny}
      previewPending={loading}
    />
  );
};
