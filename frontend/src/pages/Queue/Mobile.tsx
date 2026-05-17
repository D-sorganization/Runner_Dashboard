import { useCallback, useEffect, useMemo, useState } from "react";
import { SegmentedControl } from "../../primitives/SegmentedControl";
import { TouchButton } from "../../primitives/TouchButton";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { useHaptic } from "../../hooks/useHaptic";

import { MobileRunCard } from "./MobileRunCard";
import { MobileRunDetail } from "./MobileRunDetail";
import type {
  FilterValue,
  QueueData,
  RunDetail,
} from "./mobileTypes";
import {
  FILTER_OPTIONS,
  POLL_INTERVAL_MS,
  elapsedLabel,
  runRepo,
} from "./mobileTypes";

export function QueueMobile() {
  const [queueData, setQueueData] = useState<QueueData>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterValue>("all");
  const [refreshing, setRefreshing] = useState(false);
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [cancelling, setCancelling] = useState<Record<string, boolean>>({});
  const [cancelDone, setCancelDone] = useState<Record<string, boolean>>({});

  const haptic = useHaptic();

  const fetchQueue = useCallback(async () => {
    try {
      const resp = await fetch("/api/queue/status");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json: QueueData = await resp.json();
      setQueueData(json);
      setError(null);
    } catch (e: unknown) {
      const message =
        e instanceof Error ? e.message : "Failed to load queue data";
      setError(message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchQueue]);

  const handleRefresh = useCallback(async () => {
    haptic.medium();
    setRefreshing(true);
    await fetchQueue();
    haptic.success();
  }, [fetchQueue, haptic]);

  // Flatten in_progress and queued into a unified list with status tags.
  const allRuns: RunDetail[] = useMemo(() => {
    const inProgress = (queueData.in_progress ?? []).map((run) => ({
      run,
      status: "running" as FilterValue,
      repo: runRepo(run),
      elapsed: elapsedLabel(run),
    }));
    const queued = (queueData.queued ?? []).map((run) => ({
      run,
      status: "queued" as FilterValue,
      repo: runRepo(run),
      elapsed: elapsedLabel(run),
    }));
    // "failed" would require a separate API call; we surface the concept in the
    // filter but show an empty state since the queue endpoint only returns active runs.
    return [...inProgress, ...queued];
  }, [queueData]);

  const filtered = useMemo(() => {
    if (filter === "all") return allRuns;
    if (filter === "failed") return []; // not in this endpoint's data
    return allRuns.filter((item) => item.status === filter);
  }, [allRuns, filter]);

  const handleCancelRun = useCallback(
    async (detail: RunDetail) => {
      const key = `${detail.repo}/${detail.run.id}`;
      if (!detail.repo) return;
      setCancelling((prev) => ({ ...prev, [key]: true }));
      try {
        const resp = await fetch(
          `/api/runs/${detail.repo}/cancel/${detail.run.id}`,
          {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          },
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        setCancelDone((prev) => ({ ...prev, [key]: true }));
        haptic.success();
        setTimeout(fetchQueue, 1500);
      } catch {
        haptic.error();
      } finally {
        setCancelling((prev) => ({ ...prev, [key]: false }));
      }
    },
    [fetchQueue, haptic],
  );

  const handleRerunRun = useCallback(
    async (detail: RunDetail) => {
      if (!detail.repo || !detail.run.id) return;
      try {
        const resp = await fetch(
          `/api/runs/${detail.repo}/rerun/${detail.run.id}`,
          {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          },
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        haptic.success();
        setTimeout(fetchQueue, 1500);
      } catch {
        haptic.error();
      }
    },
    [fetchQueue, haptic],
  );

  // Loading skeleton
  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading queue"
        aria-live="polite"
        className="queue-mobile-loading"
        role="status"
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          padding: 16,
        }}
      >
        <SkeletonLine height={20} width="50%" />
        <SkeletonLine height={36} width="100%" />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  // Error state (only if no data at all)
  if (error && allRuns.length === 0) {
    return (
      <div
        aria-live="assertive"
        className="queue-mobile-error"
        role="alert"
        style={{
          color: "var(--accent-red)",
          padding: "24px",
          textAlign: "center",
        }}
      >
        <div style={{ marginBottom: 12 }}>{error}</div>
        <TouchButton onClick={fetchQueue} variant="primary">
          Retry
        </TouchButton>
      </div>
    );
  }

  return (
    <section
      aria-label="Queue and Workflows"
      className="queue-mobile"
      style={{ padding: "12px 12px 24px" }}
    >
      {/* KPI strip */}
      <div
        aria-label="Queue summary"
        className="queue-mobile-kpi-strip"
        style={{
          display: "flex",
          gap: 8,
          justifyContent: "space-around",
          marginBottom: 14,
          padding: "10px 0",
          borderBottom: "1px solid var(--border)",
        }}
      >
        {[
          {
            label: "Running",
            value: queueData.in_progress?.length ?? 0,
            color: "var(--accent-yellow)",
          },
          {
            label: "Queued",
            value: queueData.queued?.length ?? 0,
            color: "var(--accent-blue)",
          },
          {
            label: "Total",
            value: queueData.total ?? allRuns.length,
            color: "var(--text-secondary)",
          },
        ].map(({ label, value, color }) => (
          <div key={label} style={{ textAlign: "center" }}>
            <div
              style={{
                color,
                fontSize: 22,
                fontVariantNumeric: "tabular-nums",
                fontWeight: 700,
                lineHeight: 1,
              }}
            >
              {value}
            </div>
            <div
              style={{
                color: "var(--text-muted)",
                fontSize: 11,
                marginTop: 2,
                textTransform: "uppercase",
              }}
            >
              {label}
            </div>
          </div>
        ))}
      </div>

      {/* Filter tabs */}
      <SegmentedControl
        ariaLabel="Filter workflow runs"
        onChange={(v) => setFilter(v as FilterValue)}
        options={FILTER_OPTIONS}
        value={filter}
      />

      {/* Run list */}
      <div aria-live="polite" style={{ marginTop: 14 }}>
        <PullToRefresh onRefresh={handleRefresh} disabled={refreshing}>
          {filtered.length === 0 ? (
            <div
              aria-label={
                filter === "failed"
                  ? "No failed runs in queue view"
                  : `No ${filter === "all" ? "" : filter + " "}runs at this time`
              }
              className="queue-mobile-empty"
              style={{
                color: "var(--text-muted)",
                padding: "40px 0",
                textAlign: "center",
              }}
            >
              {filter === "failed"
                ? "Failed runs are not tracked in the live queue. Check the Workflows tab for run history."
                : filter === "all"
                  ? "No active workflow runs. All runners are idle."
                  : `No ${filter} runs right now.`}
            </div>
          ) : (
            filtered.map((item) => (
              <MobileRunCard
                key={`${item.repo}/${item.run.id}`}
                elapsed={item.elapsed}
                repo={item.repo}
                run={item.run}
                status={item.status}
                onClick={() => {
                  haptic.light();
                  setSelectedRun(item);
                }}
              />
            ))
          )}
        </PullToRefresh>
      </div>

      {/* Detail BottomSheet */}
      <MobileRunDetail
        selectedRun={selectedRun}
        onClose={() => setSelectedRun(null)}
        onRerun={handleRerunRun}
        onCancel={handleCancelRun}
        cancelling={cancelling}
        cancelDone={cancelDone}
      />
    </section>
  );
}
