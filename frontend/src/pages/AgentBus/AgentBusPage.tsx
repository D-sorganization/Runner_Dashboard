import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";
import { EmptyState } from "../../primitives/EmptyState";

interface AgentRow {
  session: string;
  role: string;
  provider: string;
  node: string;
  lease_expires_at: string;
  inbox_preview: Array<{ id: string; type: string; from_session: string; body: string }>;
}

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
}

export function AgentBusPage(): React.ReactElement {
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    legacyFetch("/api/v1/agent/overview")
      .then((r) => {
        if (!r.ok) throw new Error(`overview ${r.status}`);
        return r.json() as Promise<OverviewResponse>;
      })
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    load();
    const id = window.setInterval(load, 30_000);
    return () => window.clearInterval(id);
  }, [load]);

  if (error) {
    return (
      <div className="agent-bus-page">
        <p className="events-tab__degraded">Agent bus overview unavailable: {error}</p>
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
          <EmptyState title="No live sessions" description="Agents appear after session-start hook registration." />
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
                  <td>{a.session}</td>
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
