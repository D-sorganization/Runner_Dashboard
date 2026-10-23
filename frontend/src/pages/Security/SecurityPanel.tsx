/**
 * Fleet Security page (epic #2566, issue #2571).
 *
 * Four sections that load and fail independently: contract coverage
 * (fleet-peer readable), posture and findings (need `security.read`), and the
 * finding detail with triage controls (`security.triage`).
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
  ALERT_KINDS,
  type ContractsOverview,
  FINDING_STATES,
  type Finding,
  type FindingDetail,
  type PostureOverview,
  SEVERITIES,
  formatAlert,
  formatSeverityCounts,
  noteRequired,
  severityTone,
  stateLabel,
} from "./securityApi";
import { type Section, type TriageState, useSecurity } from "./useSecurity";

const ALERT_LABELS: Record<string, string> = {
  dependabot: "Dependabot",
  code_scanning: "Code scanning",
  secret_scanning: "Secret scanning", // pragma: allowlist secret
};

function SectionStatus({
  section,
  name,
  testId,
  onRetry,
}: {
  section: Exclude<Section<unknown>, { kind: "ready" }>;
  name: string;
  testId: string;
  onRetry: () => void;
}): React.ReactElement {
  if (section.kind === "loading") {
    return (
      <p role="status" className="staff-muted" data-testid={`${testId}-loading`}>
        Loading {name}…
      </p>
    );
  }
  if (section.kind === "disabled") {
    return (
      <EmptyState
        title={`${name[0].toUpperCase()}${name.slice(1)} disabled`}
        description="The security scanner API is not enabled on this node."
        data-testid={`${testId}-disabled`}
      />
    );
  }
  if (section.kind === "forbidden") {
    return (
      <EmptyState
        title={`${name[0].toUpperCase()}${name.slice(1)} requires security.read`}
        description="Your principal lacks the security.read scope, so only contract coverage is shown."
        data-testid={`${testId}-forbidden`}
      />
    );
  }
  return (
    <EmptyState
      variant="error"
      title={`${name[0].toUpperCase()}${name.slice(1)} unavailable`}
      description={section.message}
      onRetry={onRetry}
      data-testid={`${testId}-error`}
    />
  );
}

function ContractsSection({ data }: { data: ContractsOverview }): React.ReactElement {
  const { summary } = data;
  return (
    <>
      <div className="stat-row">
        <div className="stat">
          <div className="stat-label">Repos</div>
          <div className="stat-value">{summary.repos}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Enrolled</div>
          <div className="stat-value">{summary.enrolled}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Valid</div>
          <div className="stat-value">{summary.valid}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Invalid</div>
          <div className="stat-value">{summary.invalid}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Read errors</div>
          <div className="stat-value">{summary.errors}</div>
        </div>
      </div>
      {data.contracts.length === 0 ? (
        <EmptyState title="No tracked repositories" description="No repository is configured for this dashboard." />
      ) : (
        <table className="staff-table" data-testid="security-contracts-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col">Enrolled</th>
              <th scope="col">Valid</th>
              <th scope="col">Problems</th>
            </tr>
          </thead>
          <tbody>
            {data.contracts.map((row) => (
              <tr key={row.repo}>
                <th scope="row">{row.repo}</th>
                <td>{row.present ? "Yes" : "No"}</td>
                <td>
                  {row.error ? (
                    <Badge tone="warning" size="sm">read error</Badge>
                  ) : !row.present ? (
                    <Badge tone="neutral" size="sm">not enrolled</Badge>
                  ) : row.valid ? (
                    <Badge tone="success" size="sm">valid</Badge>
                  ) : (
                    <Badge tone="danger" size="sm">invalid</Badge>
                  )}
                </td>
                <td>
                  {row.error ? <span>{row.error}</span> : null}
                  {row.problems.length > 0 ? (
                    <ul style={{ margin: 0, paddingLeft: 16 }}>
                      {row.problems.map((problem, i) => (
                        <li key={i}>{problem}</li>
                      ))}
                    </ul>
                  ) : row.error ? null : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function PostureSection({ data }: { data: PostureOverview }): React.ReactElement {
  const { operator } = data;
  return (
    <>
      {!operator.real_scans_possible ? (
        <p className="staff-muted" data-testid="security-no-real-scans">
          No VM-isolated scan host is configured, so real scans cannot run yet.
        </p>
      ) : null}
      {operator.errors.length > 0 ? (
        <ul data-testid="security-operator-errors">
          {operator.errors.map((err, i) => (
            <li key={i}>{err}</li>
          ))}
        </ul>
      ) : null}
      {data.repos.length === 0 ? (
        <EmptyState title="No posture yet" description="No tracked repositories." />
      ) : (
        <table className="staff-table" data-testid="security-posture-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col">Scanning</th>
              <th scope="col">Open findings</th>
              {ALERT_KINDS.map((kind) => (
                <th scope="col" key={kind}>{ALERT_LABELS[kind]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.repos.map((row) => (
              <tr key={row.repo}>
                <th scope="row">{row.repo}</th>
                <td>{row.operator?.enabled === false ? "off" : "on"}</td>
                <td>{formatSeverityCounts(row.open_findings)}</td>
                {ALERT_KINDS.map((kind) => {
                  const alert = row.github_alerts?.[kind];
                  const breakdown =
                    alert?.status === "ok"
                      ? formatSeverityCounts(alert.by_severity)
                      : alert?.reason ?? undefined;
                  return (
                    <td key={kind} title={breakdown}>
                      {formatAlert(alert)}
                      {alert?.status === "ok" && alert.open > 0 ? (
                        <span className="staff-muted"> ({breakdown})</span>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

function FindingsTable({
  rows,
  onSelect,
}: {
  rows: Finding[];
  onSelect: (id: string) => void;
}): React.ReactElement {
  if (rows.length === 0) {
    return <EmptyState title="No findings" description="Nothing matches these filters." />;
  }
  return (
    <table className="staff-table" data-testid="security-findings-table">
      <thead>
        <tr>
          <th scope="col">Severity</th>
          <th scope="col">Finding</th>
          <th scope="col">Repository</th>
          <th scope="col">Location</th>
          <th scope="col">State</th>
          <th scope="col">Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            <td>
              <Badge tone={severityTone(row.severity)} size="sm">{row.severity}</Badge>
            </td>
            <th scope="row">
              {row.title} <span className="staff-muted">{row.cwe}</span>
            </th>
            <td>{row.repo}</td>
            <td>
              <code>{row.path}</code>
            </td>
            <td>{stateLabel(row.state)}</td>
            <td>
              <TouchButton onClick={() => onSelect(row.id)} aria-label={`Open finding ${row.title}`}>
                Details
              </TouchButton>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

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
        <TouchButton type="submit" variant="primary" disabled={blocked}>
          {triage.pending ? "Saving…" : "Apply triage"}
        </TouchButton>
      </div>
    </form>
  );
}

function FindingDrawer({
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

export function SecurityPanel(): React.ReactElement {
  const {
    contracts,
    posture,
    findings,
    filters,
    setFilters,
    refetch,
    detail,
    detailLoading,
    detailError,
    selectFinding,
    closeFinding,
    triage,
    submitTriage,
  } = useSecurity();

  const repos = contracts.kind === "ready" ? contracts.data.contracts.map((c) => c.repo).sort() : [];
  const showFindingsFilters = findings.kind === "ready" || findings.kind === "loading";

  return (
    <div data-testid="security-panel" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <TouchButton onClick={refetch}>Refresh</TouchButton>
      </div>

      <section className="card" aria-labelledby="security-contracts-heading">
        <h3 id="security-contracts-heading" style={{ marginTop: 0 }}>Contract coverage</h3>
        {contracts.kind === "ready" ? (
          <ContractsSection data={contracts.data} />
        ) : (
          <SectionStatus section={contracts} name="contract coverage" testId="security-contracts" onRetry={refetch} />
        )}
      </section>

      <section className="card" aria-labelledby="security-posture-heading">
        <h3 id="security-posture-heading" style={{ marginTop: 0 }}>Posture</h3>
        {posture.kind === "ready" ? (
          <PostureSection data={posture.data} />
        ) : (
          <SectionStatus section={posture} name="posture" testId="security-posture" onRetry={refetch} />
        )}
      </section>

      <section className="card" aria-labelledby="security-findings-heading">
        <h3 id="security-findings-heading" style={{ marginTop: 0 }}>Findings</h3>
        {showFindingsFilters ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 8 }}>
            <label>
              Repository
              <select
                aria-label="Filter findings by repository"
                value={filters.repo ?? ""}
                onChange={(e) => setFilters({ ...filters, repo: e.target.value || null })}
                style={{ marginLeft: 8 }}
              >
                <option value="">All</option>
                {repos.map((repo) => (
                  <option key={repo} value={repo}>{repo}</option>
                ))}
              </select>
            </label>
            <label>
              State
              <select
                aria-label="Filter findings by state"
                value={filters.state ?? ""}
                onChange={(e) => setFilters({ ...filters, state: e.target.value || null })}
                style={{ marginLeft: 8 }}
              >
                <option value="">All</option>
                {FINDING_STATES.map((s) => (
                  <option key={s} value={s}>{stateLabel(s)}</option>
                ))}
              </select>
            </label>
            <label>
              Severity
              <select
                aria-label="Filter findings by severity"
                value={filters.severity ?? ""}
                onChange={(e) => setFilters({ ...filters, severity: e.target.value || null })}
                style={{ marginLeft: 8 }}
              >
                <option value="">All</option>
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
        {findings.kind === "ready" ? (
          <FindingsTable rows={findings.data} onSelect={(id) => void selectFinding(id)} />
        ) : (
          <SectionStatus section={findings} name="findings" testId="security-findings" onRetry={refetch} />
        )}
      </section>

      {detail || detailLoading || detailError ? (
        <FindingDrawer
          detail={detail}
          loading={detailLoading}
          error={detailError}
          triage={triage}
          onClose={closeFinding}
          onTriage={submitTriage}
        />
      ) : null}
    </div>
  );
}
