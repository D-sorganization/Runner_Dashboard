/**
 * Maxwell/Mobile.tsx — M11 of the runner-dashboard mobile EPIC.
 *
 * Features:
 * - Status header: colored pill (running / stopped / error) + daemon version + refresh button
 * - Active tasks: compact horizontal-scroll card row (task_id, status, elapsed)
 * - Chat interface: scrollable history + text input + send; sessionStorage persistence
 * - Control sheet: Start / Stop / Restart via BottomSheet triggered by settings icon
 * - PullToRefresh refreshes status + tasks
 */
import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { BottomSheet } from "../../primitives/BottomSheet";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { TouchButton } from "../../primitives/TouchButton";
import { useHaptic } from "../../hooks/useHaptic";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MaxwellStatus {
  status?: "running" | "stopped" | "error" | string;
  http_reachable?: boolean;
  binary_found?: boolean;
  service_running?: boolean;
  service_detail?: string;
  http_detail?: string;
  binary_path?: string;
  dashboard_url?: string;
}

interface MaxwellTask {
  task_id: string;
  status: string;
  started_at?: string | number;
  elapsed_seconds?: number;
}

interface ChatMessage {
  id: number;
  role: "operator" | "maxwell";
  content: string;
  streaming?: boolean;
  error?: boolean;
}

type ControlAction = "start" | "stop" | "restart";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CHAT_STORE_KEY = "maxwellMobileChatHistory";
const MAX_HISTORY = 40;
const QUICK_CHIPS = ["status", "summarize last hour", "which runners are blocked?"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function elapsedLabel(task: MaxwellTask): string {
  if (task.elapsed_seconds !== undefined) {
    const s = Math.floor(task.elapsed_seconds);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }
  if (task.started_at) {
    const start = typeof task.started_at === "number"
      ? task.started_at
      : new Date(task.started_at).getTime();
    if (!isNaN(start)) {
      const s = Math.floor((Date.now() - start) / 1000);
      if (s < 60) return `${s}s`;
      const m = Math.floor(s / 60);
      if (m < 60) return `${m}m ${s % 60}s`;
      return `${Math.floor(m / 60)}h ${m % 60}m`;
    }
  }
  return "—";
}

function statusPillStyle(status: string): React.CSSProperties {
  const s = status.toLowerCase();
  if (s === "running") {
    return { background: "rgba(63,185,80,0.15)", color: "var(--accent-green)", border: "1px solid rgba(63,185,80,0.4)" };
  }
  if (s === "error") {
    return { background: "rgba(248,81,73,0.12)", color: "var(--accent-red)", border: "1px solid rgba(248,81,73,0.35)" };
  }
  // stopped / unknown
  return { background: "rgba(139,148,158,0.12)", color: "var(--text-muted)", border: "1px solid rgba(139,148,158,0.3)" };
}

function statusEmoji(status: string): string {
  const s = status.toLowerCase();
  if (s === "running") return "🟢";
  if (s === "error") return "🔴";
  return "🟡";
}

function loadChatHistory(): ChatMessage[] {
  try {
    return JSON.parse(sessionStorage.getItem(CHAT_STORE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveChatHistory(messages: ChatMessage[]): void {
  try {
    sessionStorage.setItem(CHAT_STORE_KEY, JSON.stringify(messages.slice(-MAX_HISTORY)));
  } catch {
    // sessionStorage may be unavailable in some contexts
  }
}

// ---------------------------------------------------------------------------
// TaskCard sub-component
// ---------------------------------------------------------------------------

interface TaskCardProps {
  task: MaxwellTask;
}

function TaskCard({ task }: TaskCardProps) {
  const s = task.status?.toLowerCase() ?? "unknown";
  const accent =
    s === "running" || s === "active"
      ? "var(--accent-green)"
      : s === "error" || s === "failed"
      ? "var(--accent-red)"
      : "var(--text-muted)";

  return (
    <div
      aria-label={`Task ${task.task_id}, status ${task.status}`}
      className="maxwell-task-card"
      role="listitem"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderLeft: `3px solid ${accent}`,
        borderRadius: 10,
        flexShrink: 0,
        minWidth: 140,
        padding: "10px 12px",
      }}
    >
      <div
        style={{
          color: "var(--text-primary)",
          fontSize: 12,
          fontWeight: 600,
          marginBottom: 4,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 120,
        }}
        title={task.task_id}
      >
        {task.task_id}
      </div>
      <div style={{ color: accent, fontSize: 11, marginBottom: 2 }}>{task.status}</div>
      <div style={{ color: "var(--text-muted)", fontSize: 11 }}>{elapsedLabel(task)}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ChatBubble sub-component
// ---------------------------------------------------------------------------

interface ChatBubbleProps {
  message: ChatMessage;
}

function ChatBubble({ message }: ChatBubbleProps) {
  const isOperator = message.role === "operator";
  return (
    <div
      className={`maxwell-chat-bubble ${message.role}${message.error ? " error" : ""}`}
      style={{
        alignSelf: isOperator ? "flex-end" : "flex-start",
        background: isOperator
          ? "var(--accent-blue)"
          : message.error
          ? "rgba(248,81,73,0.12)"
          : "var(--bg-secondary)",
        border: message.error ? "1px solid rgba(248,81,73,0.35)" : undefined,
        borderRadius: isOperator ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
        color: isOperator ? "#fff" : message.error ? "var(--accent-red)" : "var(--text-primary)",
        fontSize: 13,
        maxWidth: "84%",
        padding: "10px 14px",
        wordBreak: "break-word",
      }}
    >
      {message.content || (message.streaming ? "Receiving…" : "")}
      {message.streaming && (
        <span aria-hidden="true" style={{ color: isOperator ? "rgba(255,255,255,0.6)" : "var(--text-muted)" }}>
          {" ▌"}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function MaxwellMobile() {
  const haptic = useHaptic();

  // -- Status state -----------------------------------------------------------
  const [status, setStatus] = useState<MaxwellStatus>({});
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [daemonVersion, setDaemonVersion] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  // -- Tasks state ------------------------------------------------------------
  const [tasks, setTasks] = useState<MaxwellTask[]>([]);
  const [tasksLoading, setTasksLoading] = useState(true);

  // -- Chat state -------------------------------------------------------------
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(loadChatHistory);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const chatListRef = useRef<HTMLDivElement>(null);

  // -- Control sheet state ----------------------------------------------------
  const [controlSheetOpen, setControlSheetOpen] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [controlResult, setControlResult] = useState<{ ok: boolean; msg: string } | null>(null);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch("/api/maxwell/status");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data: MaxwellStatus = await resp.json();
      setStatus(data);
      setStatusError(null);
    } catch (e: unknown) {
      setStatusError(e instanceof Error ? e.message : "Failed to load Maxwell status");
    } finally {
      setStatusLoading(false);
    }
  }, []);

  const fetchVersion = useCallback(async () => {
    try {
      const resp = await fetch("/api/maxwell/version");
      if (!resp.ok) return;
      const data = await resp.json();
      setDaemonVersion(data.contract ?? data.daemon ?? "");
    } catch {
      setDaemonVersion("");
    }
  }, []);

  const fetchTasks = useCallback(async () => {
    setTasksLoading(true);
    try {
      const resp = await fetch("/api/maxwell/tasks?limit=10");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setTasks(data.tasks ?? []);
    } catch {
      setTasks([]);
    } finally {
      setTasksLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchStatus();
    fetchVersion();
    fetchTasks();
  }, [fetchStatus, fetchVersion, fetchTasks]);

  // Persist chat history
  useEffect(() => {
    saveChatHistory(chatMessages);
  }, [chatMessages]);

  // Auto-scroll chat when new messages arrive (unless user scrolled up)
  useEffect(() => {
    if (showScrollBtn) return;
    const el = chatListRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [chatMessages, showScrollBtn]);

  // ---------------------------------------------------------------------------
  // Pull-to-refresh
  // ---------------------------------------------------------------------------

  const handleRefresh = useCallback(async () => {
    haptic.medium();
    setRefreshing(true);
    await Promise.all([fetchStatus(), fetchTasks(), fetchVersion()]);
    setRefreshing(false);
    haptic.success();
  }, [fetchStatus, fetchTasks, fetchVersion, haptic]);

  // ---------------------------------------------------------------------------
  // Chat
  // ---------------------------------------------------------------------------

  function isNearBottom(): boolean {
    const el = chatListRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 56;
  }

  function onChatScroll() {
    setShowScrollBtn(!isNearBottom());
  }

  function updateMessage(id: number, patch: Partial<ChatMessage>) {
    setChatMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    );
  }

  const sendChat = useCallback(
    async (text?: string) => {
      const msg = (text ?? chatInput).trim();
      if (!msg || chatSending) return;
      setChatInput("");
      setShowScrollBtn(false);

      const now = Date.now();
      const userMsg: ChatMessage = { id: now, role: "operator", content: msg };
      const assistantId = now + 1;
      setChatMessages((prev) => [
        ...prev,
        userMsg,
        { id: assistantId, role: "maxwell", content: "", streaming: true },
      ]);
      setChatSending(true);

      try {
        const resp = await fetch("/api/maxwell/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
          body: JSON.stringify({ message: msg, history: chatMessages.slice(-12) }),
        });

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        // Handle SSE / streaming response
        if (resp.body && typeof window !== "undefined" && window.TextDecoder) {
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let acc = "";
          const pump = async (): Promise<string> => {
            const { done, value } = await reader.read();
            if (done) return acc;
            acc += decoder.decode(value, { stream: true });
            updateMessage(assistantId, { content: acc || "Receiving…", streaming: true });
            return pump();
          };
          const finalText = await pump();
          updateMessage(assistantId, {
            content: finalText || "Maxwell returned an empty response.",
            streaming: false,
          });
        } else {
          // Fallback: plain JSON
          const data = await resp.json();
          updateMessage(assistantId, {
            content: data.response ?? data.message ?? "Maxwell returned an empty response.",
            streaming: false,
          });
        }
      } catch (e: unknown) {
        updateMessage(assistantId, {
          content: "Maxwell-Daemon is unreachable. Check daemon status above, then retry.",
          streaming: false,
          error: true,
        });
      } finally {
        setChatSending(false);
      }
    },
    [chatInput, chatSending, chatMessages],
  );

  function onChatKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendChat();
    }
  }

  // ---------------------------------------------------------------------------
  // Daemon control
  // ---------------------------------------------------------------------------

  const handleControl = useCallback(
    async (action: ControlAction) => {
      setControlling(true);
      setControlResult(null);
      try {
        const resp = await fetch("/api/maxwell/control", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
          body: JSON.stringify({ action }),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          throw new Error(data.detail ?? `HTTP ${resp.status}`);
        }
        setControlResult({ ok: true, msg: `Requested ${action}.` });
        setTimeout(() => {
          fetchStatus();
          fetchTasks();
        }, 1000);
      } catch (e: unknown) {
        setControlResult({ ok: false, msg: e instanceof Error ? e.message : "Control failed" });
      } finally {
        setControlling(false);
      }
    },
    [fetchStatus, fetchTasks],
  );

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  const daemonStatus = (status.status ?? "unknown").toLowerCase();
  const isRunning = daemonStatus === "running";

  function renderStatusHeader() {
    const pillStyle: React.CSSProperties = {
      ...statusPillStyle(daemonStatus),
      borderRadius: 20,
      display: "inline-flex",
      alignItems: "center",
      gap: 5,
      fontSize: 12,
      fontWeight: 600,
      padding: "4px 10px",
    };

    return (
      <div
        style={{
          alignItems: "center",
          display: "flex",
          gap: 8,
          justifyContent: "space-between",
          marginBottom: 14,
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span aria-label={`Maxwell daemon status: ${daemonStatus}`} style={pillStyle}>
            <span aria-hidden="true">{statusEmoji(daemonStatus)}</span>
            {daemonStatus}
          </span>
          {daemonVersion && (
            <span style={{ color: "var(--text-muted)", fontSize: 11 }}>v{daemonVersion}</span>
          )}
          {statusError && (
            <span aria-live="assertive" role="alert" style={{ color: "var(--accent-red)", fontSize: 11 }}>
              {statusError}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <TouchButton
            aria-label="Refresh Maxwell status"
            disabled={statusLoading || refreshing}
            onClick={handleRefresh}
            variant="default"
            style={{ fontSize: 12, minHeight: 34, padding: "4px 10px" }}
          >
            {refreshing ? "Refreshing…" : "↻ Refresh"}
          </TouchButton>
          <TouchButton
            aria-label="Open daemon controls"
            data-testid="maxwell-settings-btn"
            onClick={() => setControlSheetOpen(true)}
            variant="default"
            style={{ fontSize: 12, minHeight: 34, padding: "4px 10px" }}
          >
            ⚙ Controls
          </TouchButton>
        </div>
      </div>
    );
  }

  function renderTasks() {
    if (tasksLoading) {
      return (
        <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              style={{
                background: "var(--bg-secondary)",
                border: "1px solid var(--border)",
                borderRadius: 10,
                flexShrink: 0,
                height: 72,
                minWidth: 140,
              }}
            />
          ))}
        </div>
      );
    }

    if (tasks.length === 0) {
      return (
        <div
          aria-label="No active tasks"
          style={{ color: "var(--text-muted)", fontSize: 12, padding: "8px 0" }}
        >
          {isRunning ? "No active tasks." : "Daemon not running — no task history."}
        </div>
      );
    }

    return (
      <div
        aria-label="Active tasks"
        role="list"
        style={{
          display: "flex",
          gap: 8,
          overflowX: "auto",
          paddingBottom: 6,
          WebkitOverflowScrolling: "touch" as React.CSSProperties["WebkitOverflowScrolling"],
        }}
      >
        {tasks.map((task) => (
          <TaskCard key={task.task_id} task={task} />
        ))}
      </div>
    );
  }

  function renderChat() {
    return (
      <div
        className="maxwell-chat-section"
        style={{ display: "flex", flexDirection: "column", flexGrow: 1, minHeight: 0 }}
      >
        <div
          style={{
            color: "var(--text-secondary)",
            fontSize: 12,
            fontWeight: 600,
            marginBottom: 8,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
          }}
        >
          Chat
        </div>

        {/* Message history */}
        <div
          ref={chatListRef}
          aria-label="Maxwell chat history"
          aria-live="polite"
          className="maxwell-chat-messages"
          onScroll={onChatScroll}
          style={{
            border: "1px solid var(--border)",
            borderRadius: 10,
            display: "flex",
            flexDirection: "column",
            flexGrow: 1,
            gap: 8,
            minHeight: 180,
            maxHeight: 280,
            overflowY: "auto",
            padding: "12px",
            WebkitOverflowScrolling: "touch" as React.CSSProperties["WebkitOverflowScrolling"],
          }}
        >
          {chatMessages.length === 0 ? (
            <div
              aria-label="No chat messages yet"
              style={{ color: "var(--text-muted)", fontSize: 12, textAlign: "center", margin: "auto" }}
            >
              {status.http_reachable
                ? "Ask Maxwell for fleet status, runner activity, or the next operator command."
                : "Maxwell-Daemon is unreachable. Chat history is preserved; retry when daemon is reachable."}
            </div>
          ) : (
            chatMessages.map((m) => <ChatBubble key={m.id} message={m} />)
          )}
        </div>

        {/* Scroll-to-bottom button */}
        {showScrollBtn && (
          <TouchButton
            aria-label="Scroll to latest chat message"
            onClick={() => {
              setShowScrollBtn(false);
              if (chatListRef.current) {
                chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
              }
            }}
            variant="default"
            style={{ alignSelf: "center", fontSize: 11, marginTop: 4, minHeight: 30, padding: "2px 10px" }}
          >
            Latest ↓
          </TouchButton>
        )}

        {/* Quick-action chips */}
        <div
          aria-label="Maxwell quick actions"
          style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}
        >
          {QUICK_CHIPS.map((chip) => (
            <TouchButton
              key={chip}
              aria-label={`Ask Maxwell: ${chip}`}
              disabled={chatSending}
              onClick={() => sendChat(chip)}
              variant="default"
              style={{ fontSize: 11, minHeight: 30, padding: "4px 10px" }}
            >
              {chip}
            </TouchButton>
          ))}
          {!status.http_reachable && (
            <TouchButton
              aria-label="Retry Maxwell connection"
              onClick={() => {
                fetchStatus();
                fetchTasks();
                fetchVersion();
              }}
              variant="primary"
              style={{ fontSize: 11, minHeight: 30, padding: "4px 10px" }}
            >
              Retry
            </TouchButton>
          )}
        </div>

        {/* Composer */}
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <textarea
            aria-label="Message Maxwell"
            disabled={chatSending || !status.http_reachable}
            onChange={(e) => setChatInput(e.target.value)}
            onKeyDown={onChatKeyDown}
            placeholder={
              status.http_reachable
                ? "Message Maxwell…"
                : "Daemon unreachable; retry before sending commands"
            }
            rows={1}
            value={chatInput}
            style={{
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              boxSizing: "border-box",
              color: "var(--text-primary)",
              flex: 1,
              fontFamily: "inherit",
              fontSize: 13,
              minHeight: 40,
              padding: "10px 12px",
              resize: "none",
            }}
          />
          <TouchButton
            aria-label="Send message to Maxwell"
            data-testid="maxwell-send-btn"
            disabled={chatSending || !chatInput.trim() || !status.http_reachable}
            onClick={() => sendChat()}
            variant="primary"
            style={{ flexShrink: 0, minHeight: 40, padding: "0 16px" }}
          >
            {chatSending ? "…" : "Send"}
          </TouchButton>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Loading state
  // ---------------------------------------------------------------------------

  if (statusLoading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading Maxwell"
        aria-live="polite"
        className="maxwell-mobile-loading"
        role="status"
        style={{ display: "flex", flexDirection: "column", gap: 10, padding: "16px" }}
      >
        <SkeletonLine height={22} width="50%" />
        <SkeletonLine height={18} width="30%" />
        <SkeletonCard lines={2} />
        <SkeletonCard lines={4} />
      </div>
    );
  }

  // ---------------------------------------------------------------------------
  // Main render
  // ---------------------------------------------------------------------------

  return (
    <PullToRefresh disabled={refreshing} onRefresh={handleRefresh}>
      <section
        aria-label="Maxwell daemon"
        className="maxwell-mobile"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 16,
          padding: "12px 12px 80px",
        }}
      >
        {/* Status header */}
        {renderStatusHeader()}

        {/* Daemon details */}
        {(status.dashboard_url || (!status.binary_found && !status.service_running && !status.http_reachable)) && (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {status.dashboard_url ? (
              <a
                href={status.dashboard_url}
                rel="noopener noreferrer"
                style={{ color: "var(--accent-blue)" }}
                target="_blank"
              >
                {status.dashboard_url} ↗
              </a>
            ) : (
              "Maxwell-Daemon is not detected on this machine."
            )}
          </div>
        )}

        {/* Active tasks */}
        <div>
          <div
            style={{
              color: "var(--text-secondary)",
              fontSize: 12,
              fontWeight: 600,
              marginBottom: 8,
              textTransform: "uppercase",
              letterSpacing: "0.04em",
            }}
          >
            Active Tasks
          </div>
          {renderTasks()}
        </div>

        {/* Chat */}
        {renderChat()}

        {/* Control result toast */}
        {controlResult && (
          <div
            aria-live="polite"
            role="status"
            style={{
              background: controlResult.ok ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)",
              border: `1px solid ${controlResult.ok ? "rgba(63,185,80,0.4)" : "rgba(248,81,73,0.35)"}`,
              borderRadius: 8,
              color: controlResult.ok ? "var(--accent-green)" : "var(--accent-red)",
              fontSize: 12,
              padding: "10px 12px",
            }}
          >
            {controlResult.msg}
          </div>
        )}
      </section>

      {/* Control sheet */}
      <BottomSheet
        isOpen={controlSheetOpen}
        onClose={() => {
          if (!controlling) setControlSheetOpen(false);
        }}
        title="Daemon Controls"
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <TouchButton
            aria-label="Start Maxwell daemon"
            data-testid="maxwell-ctrl-start"
            disabled={controlling || isRunning}
            onClick={() => handleControl("start")}
            variant="primary"
            style={{ minHeight: 48, width: "100%" }}
          >
            {controlling ? "Working…" : "Start Maxwell"}
          </TouchButton>
          <TouchButton
            aria-label="Stop Maxwell daemon"
            data-testid="maxwell-ctrl-stop"
            disabled={controlling || !isRunning}
            onClick={() => handleControl("stop")}
            variant="danger"
            style={{ minHeight: 48, width: "100%" }}
          >
            {controlling ? "Working…" : "Stop Maxwell"}
          </TouchButton>
          <TouchButton
            aria-label="Restart Maxwell daemon"
            data-testid="maxwell-ctrl-restart"
            disabled={controlling}
            onClick={() => handleControl("restart")}
            variant="default"
            style={{ minHeight: 48, width: "100%" }}
          >
            {controlling ? "Working…" : "Restart Maxwell"}
          </TouchButton>
        </div>
      </BottomSheet>
    </PullToRefresh>
  );
}
