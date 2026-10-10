/**
 * Maxwell/Mobile.tsx — M11 of the runner-dashboard mobile EPIC.
 *
 * Features:
 * - Status header: colored pill (running / stopped / error) + daemon version + refresh button
 * - Active tasks: compact horizontal-scroll card row (task_id, status, elapsed)
 * - Chat link: points to the Staff Console, where Maxwell chat now lives (#1338)
 * - Control sheet: Start / Stop / Restart via BottomSheet triggered by settings icon
 * - PullToRefresh refreshes status + tasks
 *
 * Sub-components are siblings (MaxwellStatusHeader, MaxwellTasks,
 * MaxwellControlSheet, TaskCard) so this file fits the 500-line cap.
 */
import { useCallback, useEffect, useState } from "react";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { useHaptic } from "../../hooks/useHaptic";

import { MaxwellControlSheet } from "./MaxwellControlSheet";
import { MaxwellStatusHeader } from "./MaxwellStatusHeader";
import { MaxwellTasks } from "./MaxwellTasks";
import type {
  ControlAction,
  MaxwellStatus,
  MaxwellTask,
} from "./mobileTypes";

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

        {/* Chat moved to the Staff Console (SC-G6, #1338) */}
        <p style={{ fontSize: 13, color: "var(--text-muted)", margin: 0 }}>
          <a href="/" style={{ color: "var(--accent-blue)" }}>
            Chat with Maxwell in the Staff Console
          </a>
        </p>

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
