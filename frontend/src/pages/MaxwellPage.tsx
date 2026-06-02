/**
 * MaxwellPage.tsx — the "Maxwell" tab, extracted (behaviour-wise 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 6).
 *
 * Surfaces the Maxwell-Daemon control plane: a status stat row (service status,
 * HTTP reachability, binary discovery, contract version), start/stop/restart
 * controls, a streaming chat console (SSE-style chunked reader with retry +
 * sessionStorage-persisted history and quick-action chips), and a recent-tasks
 * table. The daemon is reached over HTTP; the dashboard never imports from the
 * Maxwell-Daemon repo (see CLAUDE.md cross-repo rule).
 *
 * Presentational shell: the daemon `status` (and its poll) is owned by the
 * legacy App, so this page receives the already-fetched `status`, a `loading`
 * flag, an `error` string, and `onRefresh` / `onControl` callbacks. Tasks,
 * version, chat, and control-status are local state fetched directly from the
 * `/api/maxwell/*` endpoints. a11y semantics and the chat stream/retry flow
 * mirror the original legacy render exactly.
 *
 * Note: the legacy section-title icons for "Maxwell Chat" and "Recent Tasks"
 * referenced `I.messageSquare` / `I.list`, which were never defined on the
 * legacy icon map and therefore rendered nothing — that no-icon behaviour is
 * preserved here.
 */
import React, { useEffect, useRef, useState } from "react";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph, ServerGlyph } from "./decompIcons";

// ── Types ──────────────────────────────────────────────────────────────────

export interface MaxwellStatus {
  status?: string;
  service_detail?: string;
  service_running?: boolean;
  http_reachable?: boolean;
  http_detail?: string;
  binary_found?: boolean;
  binary_path?: string;
  dashboard_url?: string;
}

export interface MaxwellTask {
  id?: string;
  status?: string;
  repo?: string;
  created_at?: string;
}

interface ChatMessage {
  id: number;
  role: string;
  content: string;
  streaming?: boolean;
  error?: boolean;
  detail?: string;
}

export interface MaxwellControlPayload {
  action: string;
}

export interface MaxwellProps {
  status?: MaxwellStatus;
  loading?: boolean;
  error?: string;
  onRefresh?: () => void;
  onControl: (payload: MaxwellControlPayload) => Promise<unknown>;
}

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

const CHAT_STORE_KEY = "maxwellMobileChatHistory";

export function MaxwellTab({
  status,
  loading,
  error,
  onRefresh,
  onControl,
}: MaxwellProps): React.ReactElement {
  const st = status || {};
  const [controlStatus, setControlStatus] = useState<{ ok: boolean; msg: string } | null>(null);
  const [controlling, setControlling] = useState(false);
  const [pendingAction, setPendingAction] = useState("");
  const [tasks, setTasks] = useState<MaxwellTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [daemonVersion, setDaemonVersion] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(CHAT_STORE_KEY) || "[]");
    } catch (e) {
      return [];
    }
  });
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const chatListRef = useRef<HTMLDivElement | null>(null);
  const isRunning = st.status === "running";

  function fetchTasks(): void {
    setTasksLoading(true);
    legacyFetch("/api/maxwell/tasks?limit=10")
      .then((r) => r.json())
      .then((data: { tasks?: MaxwellTask[] }) => {
        setTasks(data.tasks || []);
      })
      .catch(() => {
        setTasks([]);
      })
      .finally(() => {
        setTasksLoading(false);
      });
  }

  function fetchVersion(): void {
    legacyFetch("/api/maxwell/version")
      .then((r) => r.json())
      .then((data: { contract?: string; daemon?: string }) => {
        setDaemonVersion(data.contract || data.daemon || "");
      })
      .catch(() => {
        setDaemonVersion("");
      });
  }

  useEffect(() => {
    fetchTasks();
    fetchVersion();
  }, []);

  useEffect(() => {
    try {
      sessionStorage.setItem(CHAT_STORE_KEY, JSON.stringify(chatMessages.slice(-40)));
    } catch (e) {
      /* ignore */
    }
  }, [chatMessages]);

  useEffect(() => {
    if (!chatListRef.current || showScrollButton) return;
    chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
  }, [chatMessages, showScrollButton]);

  function isNearChatBottom(): boolean {
    if (!chatListRef.current) return true;
    const node = chatListRef.current;
    return node.scrollHeight - node.scrollTop - node.clientHeight < 48;
  }

  function onChatScroll(): void {
    setShowScrollButton(!isNearChatBottom());
  }

  function updateChatMessage(id: number, patch: Partial<ChatMessage>): void {
    setChatMessages((prev) => prev.map((m) => (m.id === id ? Object.assign({}, m, patch) : m)));
  }

  function sendMaxwellChat(text?: string): void {
    const msg = (text || chatInput).trim();
    if (!msg || chatSending) return;
    setChatInput("");
    setShowScrollButton(false);
    const now = Date.now();
    const userMsg: ChatMessage = { id: now, role: "operator", content: msg };
    const assistantId = now + 1;
    setChatMessages((prev) =>
      prev.concat([userMsg, { id: assistantId, role: "maxwell", content: "", streaming: true }]),
    );
    setChatSending(true);
    legacyFetch("/api/maxwell/chat", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ message: msg, history: chatMessages.slice(-12) }),
    })
      .then((r) => {
        if (!r.ok) throw new Error("HTTP " + r.status);
        if (!r.body || !window.TextDecoder) return r.text();
        const reader = r.body.getReader();
        const decoder = new TextDecoder();
        let acc = "";
        function pump(): Promise<string> {
          return reader.read().then((result) => {
            if (result.done) return acc;
            acc += decoder.decode(result.value, { stream: true });
            updateChatMessage(assistantId, { content: acc || "Receiving...", streaming: true });
            return pump();
          });
        }
        return pump();
      })
      .then((streamed) => {
        updateChatMessage(assistantId, {
          content: streamed || "Maxwell returned an empty response.",
          streaming: false,
        });
      })
      .catch((err: Error) => {
        updateChatMessage(assistantId, {
          content: "Maxwell-Daemon is unreachable. Check daemon status above, then retry.",
          detail: String(err),
          streaming: false,
          error: true,
        });
      })
      .finally(() => {
        setChatSending(false);
      });
  }

  function onChatKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMaxwellChat();
    }
  }

  function doControl(action: string): void {
    setPendingAction(action);
    setControlling(true);
    setControlStatus(null);
    onControl({ action })
      .then(() => {
        setControlStatus({ ok: true, msg: "Requested " + action + "." });
        if (onRefresh) setTimeout(onRefresh, 1000);
      })
      .catch((err: Error) => {
        setControlStatus({ ok: false, msg: String(err) });
      })
      .finally(() => {
        setControlling(false);
        setPendingAction("");
      });
  }

  return (
    <div>
      <div className="stat-row">
        <Stat label="Status" value={st.status || "unknown"} sub={st.service_detail || ""} />
        <Stat label="HTTP" value={st.http_reachable ? "reachable" : "offline"} sub={st.http_detail || ""} />
        <Stat label="Binary" value={st.binary_found ? "found" : "missing"} sub={st.binary_path || "not on PATH"} />
        <Stat label="Contract" value={daemonVersion || "unknown"} />
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
        <button className="btn" onClick={onRefresh} disabled={loading} aria-label="Refresh Maxwell status">
          <RefreshGlyph size={12} />
          {loading ? "Refreshing..." : "Refresh"}
        </button>
        {!isRunning ? (
          <button
            className="btn"
            onClick={() => {
              doControl("start");
            }}
            disabled={controlling}
            aria-label="Start Maxwell daemon"
          >
            Start Maxwell
          </button>
        ) : null}
        {isRunning ? (
          <button
            className="btn"
            onClick={() => {
              doControl("stop");
            }}
            disabled={controlling}
            aria-label="Stop Maxwell daemon"
          >
            Stop Maxwell
          </button>
        ) : null}
        {isRunning ? (
          <button
            className="btn"
            onClick={() => {
              doControl("restart");
            }}
            disabled={controlling}
            aria-label="Restart Maxwell daemon"
          >
            Restart Maxwell
          </button>
        ) : null}
      </div>
      {controlStatus ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            marginBottom: 12,
            background: controlStatus.ok ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)",
            color: controlStatus.ok ? "var(--accent-green)" : "var(--accent-red)",
          }}
        >
          {controlStatus.msg}
        </div>
      ) : null}
      {error ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            marginBottom: 12,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
          }}
        >
          {error}
        </div>
      ) : null}
      <div className="section">
        <div className="section-header">
          <span className="section-title">
            <ServerGlyph size={14} />
            Maxwell-Daemon
          </span>
        </div>
        <div className="section-body">
          {pendingAction ? (
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}>
              {"Working on " + pendingAction + "..."}
            </div>
          ) : null}
          {st.dashboard_url ? (
            <a href={st.dashboard_url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent-blue)" }}>
              {st.dashboard_url + " ↗"}
            </a>
          ) : null}
          {!st.binary_found && !st.service_running && !st.http_reachable ? (
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-muted)" }}>
              Maxwell-Daemon is not detected on this machine.
            </div>
          ) : null}
        </div>
      </div>
      <div className="section maxwell-chat-section">
        <div className="section-header">
          <span className="section-title">Maxwell Chat</span>
        </div>
        <div className="section-body maxwell-chat">
          <div className="maxwell-chat-messages" ref={chatListRef} onScroll={onChatScroll} aria-live="polite">
            {chatMessages.length === 0 ? (
              <div className="maxwell-chat-empty">
                {st.http_reachable
                  ? "Ask Maxwell for fleet status, recent runner activity, or the next operator command."
                  : "Maxwell-Daemon is unreachable. Chat history is preserved; use Retry after the daemon is reachable."}
              </div>
            ) : (
              chatMessages.map((msg) => (
                <div key={msg.id} className={"maxwell-chat-bubble " + msg.role + (msg.error ? " error" : "")}>
                  {msg.content || (msg.streaming ? "Streaming..." : "")}
                  {msg.streaming ? <span style={{ color: "var(--text-muted)" }}> ▌</span> : null}
                </div>
              ))
            )}
          </div>
          {showScrollButton ? (
            <button
              aria-label="Scroll to bottom of chat"
              className="btn maxwell-scroll-button"
              onClick={() => {
                setShowScrollButton(false);
                if (chatListRef.current) chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
              }}
            >
              Latest
            </button>
          ) : null}
          <div className="maxwell-quick-actions" aria-label="Maxwell quick actions">
            {["status", "summarize last hour", "which runners are blocked?"].map((chip) => (
              <button
                key={chip}
                className="btn"
                type="button"
                onClick={() => {
                  sendMaxwellChat(chip);
                }}
                disabled={chatSending}
              >
                {chip}
              </button>
            ))}
            {!st.http_reachable ? (
              <button
                className="btn btn-blue"
                type="button"
                onClick={() => {
                  if (onRefresh) onRefresh();
                  fetchTasks();
                  fetchVersion();
                }}
              >
                Retry
              </button>
            ) : null}
          </div>
          <div className="maxwell-composer">
            <textarea
              value={chatInput}
              onChange={(e) => {
                setChatInput(e.target.value);
              }}
              onKeyDown={onChatKeyDown}
              placeholder={
                st.http_reachable ? "Message Maxwell..." : "Daemon unreachable; retry before sending commands"
              }
              rows={1}
              disabled={chatSending || !st.http_reachable}
              style={{
                width: "100%",
                boxSizing: "border-box",
                borderRadius: 8,
                border: "1px solid var(--border)",
                background: "var(--bg-tertiary)",
                color: "var(--text-primary)",
                padding: "10px 12px",
                fontFamily: "inherit",
                fontSize: 13,
              }}
            />
            <button
              className="btn btn-blue"
              type="button"
              onClick={() => {
                sendMaxwellChat();
              }}
              disabled={chatSending || !chatInput.trim() || !st.http_reachable}
            >
              {chatSending ? "Sending..." : "Send"}
            </button>
          </div>
        </div>
      </div>
      <div className="section">
        <div className="section-header">
          <span className="section-title">Recent Tasks</span>
        </div>
        <div className="section-body">
          {tasksLoading ? (
            <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading tasks…</div>
          ) : !st.http_reachable ? (
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Maxwell-Daemon offline — no task history</div>
          ) : tasks.length === 0 ? (
            <div style={{ fontSize: 12, color: "var(--text-muted)" }}>No tasks yet</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Status</th>
                  <th>Repo</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id}>
                    <td>{(t.id || "").slice(0, 8)}</td>
                    <td>{t.status || "—"}</td>
                    <td>{t.repo || "—"}</td>
                    <td>{t.created_at ? t.created_at.slice(0, 16).replace("T", " ") : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

export default MaxwellTab;
