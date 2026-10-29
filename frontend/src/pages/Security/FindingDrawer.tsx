/**
 * Finding detail and triage form for the Security page (epic #2566, #2571).
 *
 * SECURITY: findings are untrusted model output. Every free-text field is a
 * React text child — no raw HTML, no markdown, no auto-linking — and repro
 * and patch are shown in <pre> as text.
 */
import React, { useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import {
  FINDING_STATES,
  type Finding,
  type FindingDetail,
  noteRequired,
  severityTone,
  stateLabel,
} from "./securityApi";
import type { TriageState } from "./useSecurity";

function TriageControls({
  finding,
  triage,
  onSubmit,
}: {
  finding: Finding;
  triage: TriageState;
  onSubmit: (state: string, note: string) => Promise<boolean>;
}): React.ReactElement {
  // Keyed on id + state by the drawer, so a new finding or a saved triage resets the form.
  const [state, setState] = useState(finding.state);
  const [note, setNote] = useState("");
  const required = noteRequired(state);
  const blocked = triage.pending || state === finding.state || (required && !note.trim());

  return (
    <form
      data-testid="security-triage-form"
      onSubmit={(event) => {
        event.preventDefault();
        void onSubmit(state, note).then((ok) => {
          if (ok) setNote("");
        });
      }}
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
    >
      <h4 style={{ margin: 0 }}>Triage</h4>
      <label>
        New state
        <select
          value={state}
          onChange={(e) => setState(e.target.value)}
          style={{ marginLeft: 8 }}
          aria-label="Triage state"
        >
          {FINDING_STATES.map((s) => (
            <option key={s} value={s}>{stateLabel(s)}</option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {required ? "Note (required for this state)" : "Note (optional)"}
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          rows={3}
          required={required}
          aria-required={required}
          aria-label="Triage note"
        />
      </label>
      {triage.error ? (
        <p role="alert" data-testid="security-triage-error">{triage.error}</p>
      ) : null}
      <div>
        <TouchButton
          type="submit"
          variant="primary"
          disabled={blocked}
          aria-label={triage.pending ? "Saving triage decision" : "Apply triage decision"}
          title={triage.pending ? "Saving triage decision" : "Apply triage decision"}
        >
          {triage.pending ? "Saving…" : "Apply triage"}
        </TouchButton>
      </div>
    </form>
  );
}

export function FindingDrawer({
  detail,
  loading,
  error,
  triage,
  onClose,
  onTriage,
}: {
  detail: FindingDetail | null;
  loading: boolean;
  error: string | null;
  triage: TriageState;
  onClose: () => void;
  onTriage: (id: string, state: string, note: string) => Promise<boolean>;
}): React.ReactElement {
  const finding = detail?.finding;
  return (
    <section className="card" aria-labelledby="security-finding-title" data-testid="security-finding-detail">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <h3 id="security-finding-title" style={{ margin: 0 }}>
          {finding ? finding.title : "Finding"}
        </h3>
        <TouchButton onClick={onClose}>Close</TouchButton>
      </div>
      {loading ? <p role="status">Loading finding…</p> : null}
      {error ? <EmptyState variant="error" title="Finding unavailable" description={error} /> : null}
      {finding && detail ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "4px 12px", margin: 0 }}>
            <dt>Severity</dt>
            <dd style={{ margin: 0 }}>
              <Badge tone={severityTone(finding.severity)} size="sm">{finding.severity}</Badge>
            </dd>
            <dt>Confidence</dt>
            <dd style={{ margin: 0 }}>{finding.confidence}</dd>
            <dt>CWE</dt>
            <dd style={{ margin: 0 }}>{finding.cwe}</dd>
            <dt>Repository</dt>
            <dd style={{ margin: 0 }}>{finding.repo}</dd>
            <dt>Location</dt>
            <dd style={{ margin: 0 }}>
              <code>{finding.path}</code>
              {finding.symbol ? <> · <code>{finding.symbol}</code></> : null}
            </dd>
            <dt>State</dt>
            <dd style={{ margin: 0 }}>{stateLabel(finding.state)}</dd>
            {finding.last_seen ? (
              <>
                <dt>Last seen</dt>
                <dd style={{ margin: 0 }}>
                  {finding.last_seen}
                  {finding.occurrences ? ` (${finding.occurrences}×)` : ""}
                </dd>
              </>
            ) : null}
          </dl>
          <h4 style={{ margin: 0 }}>Root cause</h4>
          <p style={{ margin: 0, whiteSpace: "pre-wrap" }} data-testid="security-finding-root-cause">
            {finding.root_cause}
          </p>
          <h4 style={{ margin: 0 }}>Reproduction</h4>
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", overflowX: "auto" }} data-testid="security-finding-repro">
            {finding.repro || "—"}
          </pre>
          <h4 style={{ margin: 0 }}>Proposed patch</h4>
          <pre style={{ margin: 0, overflowX: "auto", maxHeight: 360 }} data-testid="security-finding-patch">
            {finding.patch || "—"}
          </pre>
          <h4 style={{ margin: 0 }}>Audit trail</h4>
          {detail.audit.length === 0 ? (
            <p className="staff-muted" style={{ margin: 0 }}>No triage history.</p>
          ) : (
            <ol data-testid="security-finding-audit" style={{ margin: 0, paddingLeft: 20 }}>
              {detail.audit.map((entry, i) => (
                <li key={i}>
                  {entry.ts} — {entry.actor}: {stateLabel(entry.from_state)} → {stateLabel(entry.to_state)}
                  {entry.note ? <span style={{ whiteSpace: "pre-wrap" }}>{` — ${entry.note}`}</span> : null}
                </li>
              ))}
            </ol>
          )}
          <TriageControls
            key={`${finding.id}:${finding.state}`}
            finding={finding}
            triage={triage}
            onSubmit={(state, note) => onTriage(finding.id, state, note)}
          />
        </div>
      ) : null}
    </section>
  );
}
