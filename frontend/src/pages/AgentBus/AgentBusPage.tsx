import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";
import { guidanceForFailure, guidanceForResponse, type OperatorGuidance } from "../../lib/apiErrorGuidance";
import { EmptyState } from "../../primitives/EmptyState";
import { tabIdToPath } from "../../shell/routing";

interface AgentRow {
  session: string;
  role: string;
  provider: string;
  node: string;
  lease_expires_at: string;
  inbox_preview: Array<{ id: string; type: string; from_session: string; body: string }>;
}

/** Why `agents` came back empty (#3504): mirrors the #3503 activity-sessions
 * contract so a bare empty list is never read as proof no fleet agents exist. */
type EmptyReason = "complete_empty" | "local_empty";

interface OverviewResponse {
  agents: AgentRow[];
  threads: Array<{
    thread_id: string;
    repo: string;
    work_kind: string;
    work_number: number;
    last_message: { type: string; from_session: string; to: string; body: string };
  }>;
  activity: Array<{
    id: string;
    type: string;
    from_session: string;
    to: string;
    repo: string;
    work_number: number;
    body: string;
  }>;
  backlog: {
    stale_sessions: Array<{ session: string; role: string }>;
    blocked: Array<{ id: string; repo: string; work_number: number; body: string }>;
  };
  empty_reason?: EmptyReason | null;
}

/** Deep-link into the existing #2864 Activity session inspector, never a second viewer. */
function activitySessionHref(session: string): string {
  return `${tabIdToPath("activity")}?session=${encodeURIComponent(`agent_bus:${session}`)}`;
}

export function AgentBusPage(): React.ReactElement {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<OperatorGuidance | null>(null);

  const load = useCallback(() => {
    legacyFetch("/api/v1/agent/overview")
      .then((r) => {
        if (!r.ok) {
          setError(guidanceForResponse(r));
          return null;
        }
        return r.json() as Promise<OverviewResponse>;
      })
      .then((res) => {
        if (res) {
          setData(res);
          setError(null);
        }
      })
      .catch((e: unknown) => setError(guidanceForFailure({ error: e })));
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  if (error) {
    return (
      <div className="agent-bus-page">
        <EmptyState variant="error" title={error.title} description={error.action} onRetry={load} />
      </div>
    );
  }

  if (!data) {
    return <div className="agent-bus-page">Loading agent bus…</div>;
  }

  return (
    <div className="agent-bus-page">
      <h1>Agent bus</h1>
      <p>Live fleet sessions, inboxes, and work-item threads (#2155).</p>

      <section>
        <h2>Live agents ({data.agents.length})</h2>
        {data.agents.length === 0 ? (
          data.empty_reason === "local_empty" ? (
            <>
              <EmptyState
                title="No currently live sessions"
                description="Every known session is stale or expired — not a sign no fleet agents exist."
              />
              <ul>
                {data.backlog.stale_sessions.map((s) => (
                  <li key={s.session}>{s.session}</li>
                ))}
              </ul>
            </>
          ) : (
            <EmptyState title="No live sessions" description="Agents appear after session-start hook registration." />
          )
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Session</th>
                <th>Role</th>
                <th>Node</th>
                <th>Inbox</th>
              </tr>
            </thead>
            <tbody>
              {data.agents.map((a) => (
                <tr key={a.session}>
                  <td>
                    <a href={activitySessionHref(a.session)}>{a.session}</a>
                  </td>
                  <td>{a.role}</td>
                  <td>{a.node}</td>
                  <td>
                    {a.inbox_preview.length === 0
                      ? "—"
                      : a.inbox_preview.map((m) => `${m.type}: ${m.body.slice(0, 60)}`).join("; ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>Work-item threads</h2>
        <ul>
          {data.threads.map((t) => (
            <li key={t.thread_id}>
              {t.repo}#{t.work_number} — last {t.last_message.type} from {t.last_message.from_session} →{" "}
              {t.last_message.to}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>Recent activity</h2>
        <ul>
          {data.activity.slice(0, 15).map((m) => (
            <li key={m.id}>
              {m.from_session} → {m.to}: [{m.type}] {m.repo}#{m.work_number} — {m.body.slice(0, 80)}
            </li>
          ))}
        </ul>
      </section>

      {(data.backlog.stale_sessions.length > 0 || data.backlog.blocked.length > 0) && (
        <section>
          <h2>Router backlog</h2>
          {data.backlog.stale_sessions.length > 0 && (
            <p>Stale sessions: {data.backlog.stale_sessions.map((s) => s.session).join(", ")}</p>
          )}
          {data.backlog.blocked.length > 0 && (
            <ul>
              {data.backlog.blocked.map((b) => (
                <li key={b.id}>
                  blocked {b.repo}#{b.work_number}: {b.body.slice(0, 100)}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

export default AgentBusPage;
