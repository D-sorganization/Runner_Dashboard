/**
 * MessagesPanel.tsx — board messages (#1233).
 *
 * Send: `POST /api/coordination/messages` with `{session, repo, to, text}`
 * where `to` is a session id or `*` (everyone registered in `repo`).
 * Inbox: `GET /api/coordination/inbox?session=&repo=` for any session, with
 * the advisory path/goal conflicts RM reports for it. Peer messages are
 * untrusted data: they render as plain text, never as markup or links.
 */
import { useEffect, useState } from "react";
import { Badge } from "../../primitives/Badge";
import { TouchButton } from "../../primitives/TouchButton";
import { describeError, fetchInbox, sendMessage, useResource } from "./fleetApi";
import { PanelFrame } from "./PanelFrame";

/** The session id this console speaks as on the board. */
export const OPERATOR_SESSION = "dashboard-operator";
const MAX_TEXT = 4000;

export interface MessageTarget {
  session: string;
  repo: string;
}

export interface MessagesPanelProps {
  /** Prefill "to" and "repo" (from Active work's Message button). */
  target?: MessageTarget | null;
}

function Inbox() {
  const [session, setSession] = useState("");
  const [repo, setRepo] = useState("");
  const [query, setQuery] = useState<MessageTarget | null>(null);
  const res = useResource(
    query ? (signal) => fetchInbox(query.session, query.repo || undefined, signal) : null,
    query ? `${query.session}|${query.repo}` : "none",
  );
  const messages = res.data?.messages ?? [];
  const conflicts = res.data?.conflicts ?? [];

  return (
    <div className="fleet-cmd__half" data-testid="messages-inbox">
      <h4 className="fleet-cmd__subtitle">Inbox</h4>
      <form
        className="fleet-cmd__row"
        onSubmit={(e) => {
          e.preventDefault();
          if (session.trim()) setQuery({ session: session.trim(), repo: repo.trim() });
        }}
      >
        <input
          className="form-input fleet-cmd__grow"
          aria-label="Inbox session"
          placeholder="session id"
          value={session}
          onChange={(e) => setSession(e.target.value)}
        />
        <input
          className="form-input fleet-cmd__short"
          aria-label="Inbox repo"
          placeholder="repo (optional)"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
        />
        <TouchButton type="submit" disabled={!session.trim()}>
          Show inbox
        </TouchButton>
      </form>
      {res.unavailable ? <p className="staff-muted">Inbox not available on this node — {res.unavailable}</p> : null}
      {res.error ? <p className="staff-error">{res.error}</p> : null}
      {res.loading ? <p className="staff-muted">Loading inbox...</p> : null}
      {query && res.data && !res.unavailable ? (
        messages.length === 0 && conflicts.length === 0 ? (
          <p className="staff-muted" data-testid="inbox-empty">
            No unacknowledged messages for {query.session}.
          </p>
        ) : (
          <ul className="fleet-cmd__messages">
            {conflicts.map((c) => (
              <li key={`conflict-${c.session}`} className="fleet-cmd__message fleet-cmd__conflict">
                <Badge tone="danger" size="sm">
                  conflict
                </Badge>{" "}
                with <strong>{c.agent}</strong> ({c.session}
                {c.issue ? `, #${c.issue}` : ""}): {[...c.paths, ...c.goals].join("; ")}
              </li>
            ))}
            {messages.map((m) => (
              <li key={m.id} className="fleet-cmd__message" data-testid={`message-${m.id}`}>
                <div className="staff-muted">
                  from <strong>{m.session}</strong> to {m.recipient === "*" ? `everyone in ${m.repo}` : m.recipient}
                  {m.at ? ` · ${m.at}` : ""}
                </div>
                <div className="fleet-cmd__message-text">{m.text}</div>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

export function MessagesPanel({ target }: MessagesPanelProps) {
  const [from, setFrom] = useState(OPERATOR_SESSION);
  const [repo, setRepo] = useState(target?.repo ?? "");
  const [to, setTo] = useState(target?.session ?? "*");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (target) {
      setTo(target.session);
      setRepo(target.repo);
    }
  }, [target]);

  const canSend = Boolean(from.trim() && repo.trim() && to.trim() && text.trim()) && !busy;

  const send = () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    sendMessage({ session: from.trim(), repo: repo.trim(), to: to.trim(), text: text.trim() })
      .then(() => {
        setNotice(`Sent to ${to.trim() === "*" ? `everyone in ${repo.trim()}` : to.trim()}.`);
        setText("");
      })
      .catch((e: unknown) => setError(describeError(e)))
      .finally(() => setBusy(false));
  };

  return (
    <PanelFrame title="Messages" testId="fleet-messages">
      <div className="fleet-cmd__split">
        <form
          className="fleet-cmd__half fleet-cmd__form"
          onSubmit={(e) => {
            e.preventDefault();
            if (canSend) send();
          }}
        >
          <h4 className="fleet-cmd__subtitle">Send</h4>
          <label className="form-label" htmlFor="fleet-msg-from">
            From session
          </label>
          <input id="fleet-msg-from" className="form-input" value={from} onChange={(e) => setFrom(e.target.value)} />
          <label className="form-label" htmlFor="fleet-msg-repo">
            Repo
          </label>
          <input
            id="fleet-msg-repo"
            className="form-input"
            placeholder="bare repository name"
            value={repo}
            onChange={(e) => setRepo(e.target.value)}
          />
          <label className="form-label" htmlFor="fleet-msg-to">
            To
          </label>
          <input
            id="fleet-msg-to"
            className="form-input"
            placeholder="session id, or * for everyone in the repo"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
          <label className="form-label" htmlFor="fleet-msg-text">
            Message
          </label>
          <textarea
            id="fleet-msg-text"
            className="form-input"
            rows={3}
            maxLength={MAX_TEXT}
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="fleet-cmd__row">
            <TouchButton type="submit" variant="primary" disabled={!canSend}>
              {busy ? "Sending..." : "Send"}
            </TouchButton>
            <span className="staff-muted">
              {text.length}/{MAX_TEXT}
            </span>
          </div>
          {error ? (
            <p className="staff-error" role="alert">
              {error}
            </p>
          ) : null}
          {notice ? (
            <p className="fleet-cmd__notice" role="status">
              {notice}
            </p>
          ) : null}
        </form>
        <Inbox />
      </div>
    </PanelFrame>
  );
}

export default MessagesPanel;
