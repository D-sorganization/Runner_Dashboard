/**
 * Maxwell/Mobile.tsx — M11 of the runner-dashboard mobile EPIC.
 *
 * Features:
 * - Status header: colored pill (running / stopped / error) + daemon version + refresh button
 * - Active tasks: compact horizontal-scroll card row (task_id, status, elapsed)
 * - Chat interface: scrollable history + text input + send; sessionStorage persistence
 * - Control sheet: Start / Stop / Restart via BottomSheet triggered by settings icon
 * - PullToRefresh refreshes status + tasks
 *
 * Sub-components are siblings (MaxwellStatusHeader, MaxwellTasks, MaxwellChat,
 * MaxwellControlSheet, TaskCard, ChatBubble) so this file fits the 500-line cap.
 */
import { useCallback, useEffect, useState } from "react";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { useHaptic } from "../../hooks/useHaptic";

import { MaxwellChat } from "./MaxwellChat";
import { MaxwellControlSheet } from "./MaxwellControlSheet";
import { MaxwellStatusHeader } from "./MaxwellStatusHeader";
import { MaxwellTasks } from "./MaxwellTasks";
import type {
  ChatMessage,
  ControlAction,
  MaxwellStatus,
  MaxwellTask,
} from "./mobileTypes";
import { loadChatHistory, saveChatHistory } from "./mobileTypes";

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

  // -- Control sheet state ----------------------------------------------------
  const [controlSheetOpen, setControlSheetOpen] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [controlResult, setControlResult] = useState<
    { ok: boolean; msg: string } | null
  >(null);

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
      setStatusError(
        e instanceof Error ? e.message : "Failed to load Maxwell status",
      );
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

  const updateMessage = useCallback(
    (id: number, patch: Partial<ChatMessage>) => {
      setChatMessages((prev) =>
        prev.map((m) => (m.id === id ? { ...m, ...patch } : m)),
      );
    },
    [],
  );

  const sendChat = useCallback(
    async (text?: string) => {
      const msg = (text ?? chatInput).trim();
      if (!msg || chatSending) return;
      setChatInput("");

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
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          body: JSON.stringify({
            message: msg,
            history: chatMessages.slice(-12),
          }),
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
            updateMessage(assistantId, {
              content: acc || "Receiving…",
              streaming: true,
            });
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
            content:
              data.response ??
              data.message ??
              "Maxwell returned an empty response.",
            streaming: false,
          });
        }
      } catch {
        updateMessage(assistantId, {
          content:
            "Maxwell-Daemon is unreachable. Check daemon status above, then retry.",
          streaming: false,
          error: true,
        });
      } finally {
        setChatSending(false);
      }
    },
    [chatInput, chatSending, chatMessages, updateMessage],
  );

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
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
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
        setControlResult({
          ok: false,
          msg: e instanceof Error ? e.message : "Control failed",
        });
      } finally {
        setControlling(false);
      }
    },
    [fetchStatus, fetchTasks],
  );

  const retryConnection = useCallback(() => {
    fetchStatus();
    fetchTasks();
    fetchVersion();
  }, [fetchStatus, fetchTasks, fetchVersion]);

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const daemonStatus = (status.status ?? "unknown").toLowerCase();
  const isRunning = daemonStatus === "running";

  if (statusLoading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading Maxwell"
        aria-live="polite"
        className="maxwell-mobile-loading"
        role="status"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 10,
          padding: "16px",
        }}
      >
        <SkeletonLine height={22} width="50%" />
        <SkeletonLine height={18} width="30%" />
        <SkeletonCard lines={2} />
        <SkeletonCard lines={4} />
      </div>
    );
  }

  const showDaemonDetails =
    status.dashboard_url ||
    (!status.binary_found && !status.service_running && !status.http_reachable);

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
        <MaxwellStatusHeader
          daemonStatus={daemonStatus}
          daemonVersion={daemonVersion}
          statusError={statusError}
          statusLoading={statusLoading}
          refreshing={refreshing}
          onRefresh={handleRefresh}
          onOpenControls={() => setControlSheetOpen(true)}
        />

        {/* Daemon details */}
        {showDaemonDetails && (
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
          <MaxwellTasks
            tasksLoading={tasksLoading}
            tasks={tasks}
            isRunning={isRunning}
          />
        </div>

        {/* Chat */}
        <MaxwellChat
          status={status}
          chatMessages={chatMessages}
          chatInput={chatInput}
          setChatInput={setChatInput}
          chatSending={chatSending}
          sendChat={sendChat}
          onRetry={retryConnection}
        />

        {/* Control result toast */}
        {controlResult && (
          <div
            aria-live="polite"
            role="status"
            style={{
              background: controlResult.ok
                ? "rgba(63,185,80,0.12)"
                : "rgba(248,81,73,0.12)",
              border: `1px solid ${controlResult.ok ? "rgba(63,185,80,0.4)" : "rgba(248,81,73,0.35)"}`,
              borderRadius: 8,
              color: controlResult.ok
                ? "var(--accent-green)"
                : "var(--accent-red)",
              fontSize: 12,
              padding: "10px 12px",
            }}
          >
            {controlResult.msg}
          </div>
        )}
      </section>

      <MaxwellControlSheet
        isOpen={controlSheetOpen}
        onClose={() => {
          if (!controlling) setControlSheetOpen(false);
        }}
        controlling={controlling}
        isRunning={isRunning}
        onControl={handleControl}
      />
    </PullToRefresh>
  );
}
