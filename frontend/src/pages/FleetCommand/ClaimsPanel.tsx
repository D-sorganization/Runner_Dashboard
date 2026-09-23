/**
 * ClaimsPanel.tsx — issue claims / leases (#1233).
 *
 * Check: `GET /api/coordination/claims?repo=&issue=` (RM `check_agent_claim`).
 * Claim: `POST /api/coordination/claims`; a claim another agent holds is a
 * 409 whose `detail.held_by` is shown verbatim. Release:
 * `POST /api/coordination/claims/release`. Every write re-checks the claim so
 * the status line always reflects the board, not the local guess.
 */
import { useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import { checkClaim, describeError, expiryLabel, parseIssue, postClaim, releaseClaim, useResource } from "./fleetApi";
import { OPERATOR_SESSION } from "./MessagesPanel";
import { PanelFrame } from "./PanelFrame";

interface Target {
  repo: string;
  issue: number;
}

export function ClaimsPanel() {
  const [repo, setRepo] = useState("");
  const [issue, setIssue] = useState("");
  const [target, setTarget] = useState<Target | null>(null);
  const [session, setSession] = useState(OPERATOR_SESSION);
  const [agent, setAgent] = useState("");
  const [intent, setIntent] = useState("implement");
  const [reason, setReason] = useState("work completed");
  const [busy, setBusy] = useState<"claim" | "release" | null>(null);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const status = useResource(
    target ? (signal) => checkClaim(target.repo, target.issue, signal) : null,
    target ? `${target.repo}#${target.issue}` : "none",
  );

  const issueNumber = parseIssue(issue);
  const ready = Boolean(repo.trim()) && issueNumber !== null;

  /** Re-read the claim for the current inputs (after a write, too). */
  const refresh = () => {
    if (!ready || issueNumber === null) return;
    const next = { repo: repo.trim(), issue: issueNumber };
    if (target && target.repo === next.repo && target.issue === next.issue) status.reload();
    else setTarget(next);
  };

  const check = () => {
    setWriteError(null);
    setNotice(null);
    refresh();
  };

  const write = (kind: "claim" | "release") => {
    if (!ready || issueNumber === null) return;
    const base = {
      repo: repo.trim(),
      issue: issueNumber,
      session: session.trim(),
      ...(agent.trim() ? { agent: agent.trim() } : {}),
    };
    setBusy(kind);
    setWriteError(null);
    setNotice(null);
    const call =
      kind === "claim"
        ? postClaim({ ...base, intent: intent.trim() || "implement" })
        : releaseClaim({ ...base, reason: reason.trim() || "work completed" });
    call
      .then(() =>
        setNotice(kind === "claim" ? `Claimed ${base.repo}#${base.issue}.` : `Released ${base.repo}#${base.issue}.`),
      )
      .catch((e: unknown) => setWriteError(describeError(e)))
      .finally(() => {
        setBusy(null);
        refresh();
      });
  };

  const data = status.data;

  return (
    <PanelFrame title="Claims" testId="fleet-claims">
      <form
        className="fleet-cmd__row"
        onSubmit={(e) => {
          e.preventDefault();
          check();
        }}
      >
        <input
          className="form-input"
          aria-label="Claim repo"
          placeholder="repo"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
        />
        <input
          className="form-input fleet-cmd__short"
          aria-label="Claim issue"
          placeholder="issue #"
          inputMode="numeric"
          value={issue}
          onChange={(e) => setIssue(e.target.value)}
        />
        <TouchButton type="submit" disabled={!ready}>
          Check claim
        </TouchButton>
      </form>

      {target ? (
        <div className="fleet-cmd__claim-status" data-testid="claim-status" aria-live="polite">
          {status.loading ? (
            <span className="staff-muted">
              Checking {target.repo}#{target.issue}...
            </span>
          ) : null}
          {status.unavailable ? (
            <span className="staff-muted">Claims not available on this node — {status.unavailable}</span>
          ) : null}
          {status.error ? <span className="staff-error">{status.error}</span> : null}
          {data && !status.unavailable && !status.loading ? (
            data.held ? (
              <>
                <Badge tone="warning" size="sm">
                  held
                </Badge>{" "}
                {target.repo}#{target.issue} is claimed by <strong>{data.agent || "unknown"}</strong>
                {data.expires_at ? ` (${expiryLabel(data.expires_at, "expires") || data.expires_at})` : ""}
                {data.reason ? <span className="staff-muted"> — {data.reason}</span> : null}
              </>
            ) : (
              <>
                <Badge tone="success" size="sm">
                  free
                </Badge>{" "}
                {target.repo}#{target.issue} has no active claim
                {data.reason ? <span className="staff-muted"> — {data.reason}</span> : null}
              </>
            )
          ) : null}
        </div>
      ) : (
        <p className="staff-muted">Check an issue before claiming it. Claims are binding; presence is advisory.</p>
      )}

      <div className="fleet-cmd__grid">
        <label className="form-label">
          Session
          <input
            className="form-input"
            aria-label="Claim session"
            value={session}
            onChange={(e) => setSession(e.target.value)}
          />
        </label>
        <label className="form-label">
          Agent
          <input
            className="form-input"
            aria-label="Claim agent"
            placeholder="defaults to your principal"
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
          />
        </label>
        <label className="form-label">
          Intent
          <input
            className="form-input"
            aria-label="Claim intent"
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
          />
        </label>
        <label className="form-label">
          Release reason
          <input
            className="form-input"
            aria-label="Release reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </label>
      </div>
      <div className="fleet-cmd__row">
        <TouchButton
          variant="primary"
          disabled={!ready || !session.trim() || busy !== null}
          onClick={() => write("claim")}
        >
          {busy === "claim" ? "Claiming..." : "Claim"}
        </TouchButton>
        <TouchButton
          variant="danger"
          disabled={!ready || !session.trim() || busy !== null}
          onClick={() => write("release")}
        >
          {busy === "release" ? "Releasing..." : "Release"}
        </TouchButton>
      </div>
      {writeError ? (
        <p className="staff-error" role="alert" data-testid="claim-error">
          {writeError}
        </p>
      ) : null}
      {notice ? (
        <p className="fleet-cmd__notice" role="status">
          {notice}
        </p>
      ) : null}
    </PanelFrame>
  );
}

export default ClaimsPanel;
