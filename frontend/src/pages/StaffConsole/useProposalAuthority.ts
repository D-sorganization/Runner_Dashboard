/**
 * useProposalAuthority: the viewer's read-only approval preview of one pending proposal (#1807).
 *
 * The preview (`can_approve`, `denial_reason`, `params_hash`) is per viewer, so it is fetched here
 * instead of riding on the shared ActionCard. It is advisory: the server re-evaluates authority
 * and re-verifies the hash on every decision. A failed fetch leaves the card as it was (no hash,
 * so an approve is refused client-side) and never grants anything.
 */
import { useCallback, useEffect, useState } from "react";
import { getActionProposal, type ProposalPreview } from "../Staff/staffApi";

export interface ProposalAuthority {
  preview: ProposalPreview | null;
  loading: boolean;
  /** Fetch the preview again, e.g. after the server refused a decision. */
  refresh: () => void;
}

export function useProposalAuthority(
  proposalId: string,
  enabled: boolean,
): ProposalAuthority {
  const [preview, setPreview] = useState<ProposalPreview | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    Promise.resolve()
      .then(() => getActionProposal(proposalId, controller.signal))
      .then((next) => {
        if (!controller.signal.aborted) setPreview(next ?? null);
      })
      .catch(() => {
        if (!controller.signal.aborted) setPreview(null);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [proposalId, enabled, nonce]);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  return { preview, loading, refresh };
}
