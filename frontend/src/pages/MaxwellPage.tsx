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
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph, ServerGlyph } from "./decompIcons";
import {
  MaxwellChatPanel,
  MaxwellTasksPanel,
  type ChatMessage,
} from "./MaxwellPanels";

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

export function MaxwellPage(): React.ReactElement {
  const [status, setStatus] = useState<MaxwellStatus>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>();

  const fetchStatus = useCallback(() => {
    setLoading(true);
    legacyFetch("/api/maxwell/status")
      .then((r) => r.json())
      .then((data: MaxwellStatus) => {
        setStatus(data || {});
        setError(undefined);
      })
      .catch(() => {
        setError("Failed to probe Maxwell status.");
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const controlDaemon = useCallback((payload: MaxwellControlPayload) => {
    return legacyFetch("/api/maxwell/control", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify(payload),
    }).then((r) =>
      r.json().then((data: unknown) => {
        if (!r.ok) {
          const detail =
            data &&
            typeof data === "object" &&
            "detail" in data &&
            typeof data.detail === "string"
              ? data.detail
              : "Control failed";
          throw new Error(detail);
        }
        return data;
      }),
    );
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  return (
    <MaxwellTab
      status={status}
      loading={loading}
      error={error}
      onRefresh={fetchStatus}
      onControl={controlDaemon}
    />
  );
}

export function MaxwellTab({
  status,
  loading,
  error,
  onRefresh,
  onControl,
}: MaxwellProps): React.ReactElement {
  const st = status || {};
  const [controlStatus, setControlStatus] = useState<{
    ok: boolean;
    msg: string;
  } | null>(null);
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
      sessionStorage.setItem(
        CHAT_STORE_KEY,
        JSON.stringify(chatMessages.slice(-40)),
      );
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
    setChatMessages((prev) =>
      prev.map((m) => (m.id === id ? Object.assign({}, m, patch) : m)),
    );
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
      prev.concat([
        userMsg,
        { id: assistantId, role: "maxwell", content: "", streaming: true },
      ]),
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
            updateChatMessage(assistantId, {
              content: acc || "Receiving...",
              streaming: true,
            });
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
          content:
            "Maxwell-Daemon is unreachable. Check daemon status above, then retry.",
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
        <Stat
          label="Status"
          value={st.status || "unknown"}
          sub={st.service_detail || ""}
        />
        <Stat
          label="HTTP"
          value={st.http_reachable ? "reachable" : "offline"}
          sub={st.http_detail || ""}
        />
        <Stat
          label="Binary"
          value={st.binary_found ? "found" : "missing"}
          sub={st.binary_path || "not on PATH"}
        />
        <Stat label="Contract" value={daemonVersion || "unknown"} />
      </div>
      <div
        style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}
      >
        <button
          className="btn"
          onClick={onRefresh}
          disabled={loading}
          aria-label="Refresh Maxwell status"
        >
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
            background: controlStatus.ok
              ? "rgba(63,185,80,0.12)"
              : "rgba(248,81,73,0.12)",
            color: controlStatus.ok
              ? "var(--accent-green)"
              : "var(--accent-red)",
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
            <div
              style={{
                fontSize: 12,
                color: "var(--text-muted)",
                marginBottom: 8,
              }}
            >
              {"Working on " + pendingAction + "..."}
            </div>
          ) : null}
          {st.dashboard_url ? (
            <a
              href={st.dashboard_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--accent-blue)" }}
            >
              {st.dashboard_url + " ↗"}
            </a>
          ) : null}
          {!st.binary_found && !st.service_running && !st.http_reachable ? (
            <div
              style={{
                marginTop: 12,
                fontSize: 12,
                color: "var(--text-muted)",
              }}
            >
              Maxwell-Daemon is not detected on this machine.
            </div>
          ) : null}
        </div>
      </div>
      <MaxwellChatPanel
        status={st}
        chatMessages={chatMessages}
        chatInput={chatInput}
        chatSending={chatSending}
        showScrollButton={showScrollButton}
        chatListRef={chatListRef}
        onChatScroll={onChatScroll}
        onChatInputChange={setChatInput}
        onChatKeyDown={onChatKeyDown}
        onSendChat={sendMaxwellChat}
        onShowLatest={() => {
          setShowScrollButton(false);
          if (chatListRef.current)
            chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
        }}
        onRetry={() => {
          if (onRefresh) onRefresh();
          fetchTasks();
          fetchVersion();
        }}
      />
      <MaxwellTasksPanel
        status={st}
        tasks={tasks}
        tasksLoading={tasksLoading}
      />
    </div>
  );
}

export default MaxwellTab;
