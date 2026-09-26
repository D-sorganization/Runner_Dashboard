/**
 * RunDetail.tsx — one staff run with its events (#1198).
 *
 * Loads `GET /api/staff/runs/{id}` and, while the run is alive
 * (queued / preparing / running), tails `GET /api/staff/runs/{id}/stream`
 * with an `EventSource`, appending each SSE event. The backend names each
 * SSE event after the store `kind`, so listeners are registered for every
 * kind the runner and the JSON-lines adapters emit; the terminal `end` event
 * closes the stream and refetches the full record (which also picks up any
 * event whose kind was not in the listener list).
 *
 * Cancel posts `POST /api/staff/runs/{id}/cancel` through the shared client
 * (CSRF header included).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import { TouchButton } from "../../primitives/TouchButton";
import { GITHUB_ORG } from "../FleetCommand/fleetApi";
import {
  invalidateStaffQueries,
  updateStaffRunFromEvent,
  useResolvedQueryClient,
} from "../../hooks/useStaffQueries";
import {
  ACTIVE_STATUSES,
  cancelRun,
  errorMessage,
  fetchRun,
  formatUsd,
  runStreamUrl,
  statusTone,
  STREAM_EVENT_KINDS,
  type RunEvent,
  type RunRecord,
} from "./staffApi";

export interface RunDetailProps {
  runId: string;
  onBack: () => void;
}

function pullUrl(repo: string, pr: number): string {
  const full = repo.includes("/") ? repo : `${GITHUB_ORG}/${repo}`;
  return `https://github.com/${full}/pull/${pr}`;
}

function mergeEvents(existing: RunEvent[], incoming: RunEvent): RunEvent[] {
  if (existing.some((ev) => ev.seq === incoming.seq)) return existing;
  return [...existing, incoming];
}

export function RunDetail({ runId, onBack }: RunDetailProps) {
  const client = useResolvedQueryClient();
  const [run, setRun] = useState<RunRecord | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(false);
  const logRef = useRef<HTMLPreElement | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) => {
      fetchRun(runId, signal)
        .then((data) => {
          setRun(data.run);
          setEvents(data.events);
          setError(null);
        })
        .catch((e: unknown) => {
          if (signal?.aborted) return;
          setError(errorMessage(e));
        });
    },
    [runId],
  );

  useEffect(() => {
    const controller = new AbortController();
    setRun(null);
    setEvents([]);
    load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const isActive = run !== null && ACTIVE_STATUSES.has(run.status);

  // Tail the SSE stream while the run is alive.
  useEffect(() => {
    if (!isActive || typeof EventSource === "undefined") return undefined;
    const lastSeq = events.length ? events[events.length - 1].seq : 0;
    const source = new EventSource(runStreamUrl(runId, lastSeq));
    setLive(true);
    const onEvent = (msg: MessageEvent<string>) => {
      try {
        const ev = JSON.parse(msg.data) as RunEvent;
        setEvents((prev) => mergeEvents(prev, ev));
        updateStaffRunFromEvent(client, runId, ev);
      } catch {
        // Ignore malformed frames; the final refetch is authoritative.
      }
    };
    for (const kind of STREAM_EVENT_KINDS) source.addEventListener(kind, onEvent as EventListener);
    source.addEventListener("end", () => {
      source.close();
      setLive(false);
      load();
    });
    source.onerror = () => {
      source.close();
      setLive(false);
      load();
    };
    return () => {
      source.close();
      setLive(false);
    };
    // `events` is intentionally excluded: the cursor is read once per (re)open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, isActive, runId, load]);

  useEffect(() => {
    const node = logRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events.length]);

  const onCancel = useCallback(() => {
    setBusy(true);
    cancelRun(runId)
      .then((data) => {
        if (data.run) setRun(data.run);
        setBusy(false);
        invalidateStaffQueries(client);
        load();
      })
      .catch((e: unknown) => {
        setError(errorMessage(e));
        setBusy(false);
      });
  }, [client, runId, load]);

  if (error && !run) {
    return (
      <div className="glass-card staff-panel">
        <EmptyState variant="error" title="Failed to load run" description={error} onRetry={() => load()} />
        <TouchButton onClick={onBack}>Back to runs</TouchButton>
      </div>
    );
  }
  if (!run) {
    return (
      <div className="glass-card staff-panel" aria-busy="true">
        <p className="staff-muted">Loading run {runId}...</p>
      </div>
    );
  }

  return (
    <div className="glass-card staff-panel staff-run" data-testid="run-detail">
      <div className="staff-panel__header">
        <div className="staff-run__heading">
          <TouchButton onClick={onBack} aria-label="Back to runs">
            ← Runs
          </TouchButton>
          <h3 className="staff-panel__title">
            {run.role} · {run.provider}
            {run.model ? ` (${run.model})` : ""}
          </h3>
          <Badge tone={statusTone(run.status)} size="sm" data-testid="run-status">
            {run.status}
          </Badge>
          {live ? (
            <Badge tone="info" size="sm">
              live
            </Badge>
          ) : null}
        </div>
        {isActive ? (
          <TouchButton variant="danger" onClick={onCancel} disabled={busy}>
            Cancel
          </TouchButton>
        ) : null}
      </div>
      {error ? <p className="staff-error">{error}</p> : null}

      <dl className="staff-run__facts">
        <dt>Run id</dt>
        <dd>
          <code>{run.id}</code>
        </dd>
        <dt>Machine</dt>
        <dd>{run.machine}</dd>
        <dt>Repo</dt>
        <dd>{run.repo || "—"}</dd>
        <dt>Target</dt>
        <dd>
          {run.target_kind} {run.target_ref}
        </dd>
        <dt>Branch</dt>
        <dd>{run.branch || "—"}</dd>
        <dt>Requested by</dt>
        <dd>{run.requested_by || "—"}</dd>
        <dt>Created</dt>
        <dd>{run.created_at}</dd>
        <dt>Ended</dt>
        <dd>{run.ended_at ?? "—"}</dd>
        <dt>Exit code</dt>
        <dd>{run.exit_code ?? "—"}</dd>
        <dt>Cost</dt>
        <dd>
          {formatUsd(run.cost_usd)} ({run.input_tokens} in / {run.output_tokens} out)
        </dd>
        {run.strategy_mode ? (
          <>
            <dt>Strategy</dt>
            <dd data-testid="run-strategy">{run.strategy_mode}</dd>
          </>
        ) : null}
        {run.outcome ? (
          <>
            <dt>Outcome</dt>
            <dd data-testid="run-outcome">{run.outcome}</dd>
          </>
        ) : null}
        {run.error ? (
          <>
            <dt>Error</dt>
            <dd className="staff-error">{run.error}</dd>
          </>
        ) : null}
        {run.failure_class ? (
          <>
            <dt>Failure Class</dt>
            <dd data-testid="run-failure-class">
              <code>{run.failure_class}</code>
              {run.retryable ? " (retryable)" : ""}
            </dd>
          </>
        ) : null}
        {run.remediation ? (
          <>
            <dt>Remediation</dt>
            <dd data-testid="run-remediation">{run.remediation}</dd>
          </>
        ) : null}
        {run.verification ? (
          <>
            <dt>Verification</dt>
            <dd data-testid="run-verification">
              <code>{run.verification}</code> {run.verification_detail}
              {run.pr_number ? (
                <>
                  {" "}
                  <a href={pullUrl(run.repo ?? "", run.pr_number)} target="_blank" rel="noreferrer">
                    #{run.pr_number}
                  </a>
                </>
              ) : null}
            </dd>
          </>
        ) : null}
      </dl>

      <details className="staff-run__prompt">
        <summary>Prompt</summary>
        <pre className="staff-pre">{run.prompt}</pre>
      </details>

      <h4 className="staff-run__events-title">Events ({events.length})</h4>
      <pre className="staff-pre staff-run__events" ref={logRef} data-testid="run-events" aria-live="polite">
        {events.map((ev) => (
          <div key={ev.seq} className="staff-run__event" data-kind={ev.kind}>
            <span className="staff-run__event-kind">[{ev.kind}]</span> {ev.text}
          </div>
        ))}
      </pre>
    </div>
  );
}

export default RunDetail;
