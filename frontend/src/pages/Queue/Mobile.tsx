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
  StaleCandidate,
  WorkflowRun,
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

  const [staleData, setStaleData] = useState<StaleCandidate[]>([]);
  const [minAge, setMinAge] = useState(60);
  const [repoFilter, setRepoFilter] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [purging, setPurging] = useState(false);

  const haptic = useHaptic();

  const fetchStale = useCallback(async () => {
    try {
      let url = `/api/queue/stale?min_age_minutes=${minAge}`;
      if (repoFilter) {
        url += `&repo=${encodeURIComponent(repoFilter)}`;
      }
      const resp = await fetch(url);
      if (resp.ok) {
        const json = await resp.json();
        setStaleData(json.runs || []);
      }
    } catch (e) {
      // eslint-disable-next-line no-console
      console.error("Failed to fetch stale runs", e);
    }
  }, [minAge, repoFilter]);

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

  const executePurge = useCallback(async () => {
    if (!confirmPurge) {
      setConfirmPurge(true);
      setTimeout(() => setConfirmPurge(false), 5000);
      return;
    }
    setConfirmPurge(false);
    setPurging(true);
    haptic.medium();

    try {
      const resp = await fetch("/api/queue/purge-stale", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          min_age_minutes: minAge,
          repo: repoFilter || null,
          dry_run: dryRun,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      haptic.success();
      fetchStale();
      fetchQueue();
    } catch (e) {
      haptic.error();
      // eslint-disable-next-line no-console
      console.error(e);
    } finally {
      setPurging(false);
    }
  }, [confirmPurge, minAge, repoFilter, dryRun, fetchStale, fetchQueue, haptic]);

  useEffect(() => {
    fetchQueue();
    const interval = setInterval(fetchQueue, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchQueue]);

  useEffect(() => {
    fetchStale();
    const interval = setInterval(fetchStale, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [fetchStale]);

  const handleRefresh = useCallback(async () => {
    haptic.medium();
    setRefreshing(true);
    await Promise.all([fetchQueue(), fetchStale()]);
    haptic.success();
  }, [fetchQueue, fetchStale, haptic]);

  // Flatten in_progress, queued and stale into a unified list with status tags.
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
    const stale = (staleData ?? []).map((run) => ({
      run: {
        id: run.run_id,
        name: run.workflow,
        head_branch: run.branch,
        html_url: run.url,
        created_at: new Date(Date.now() - run.age_minutes * 60 * 1000).toISOString(),
        repository: { name: run.repo },
        stale_reason: run.reason,
        safe_to_cancel: run.safe_to_cancel,
        current_head_sha: run.current_head_sha,
        run_head_sha: run.run_head_sha,
        pr_number: run.pr_number,
        age_minutes: run.age_minutes,
      } as WorkflowRun,
      status: "stale" as FilterValue,
      repo: run.repo,
      elapsed: `${run.age_minutes}m`,
    }));
    return [...inProgress, ...queued, ...stale];
  }, [queueData, staleData]);

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
            label: "Stale",
            value: staleData.length,
            color: "var(--accent-orange)",
          },
          {
            label: "Total",
            value: queueData.total ?? (allRuns.length - staleData.length),
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

      {filter === "stale" && (
        <div
          style={{
            background: "var(--bg-secondary)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            padding: 14,
            marginTop: 14,
            marginBottom: 0,
            display: "flex",
            flexDirection: "column",
            gap: 12,
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
            Stale Cleanup Controls
          </div>
          
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 45%", display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Min Age (min)</span>
              <input
                type="number"
                value={minAge}
                onChange={(e) => setMinAge(Number(e.target.value) || 0)}
                style={{
                  padding: "6px 10px",
                  fontSize: 13,
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "var(--bg-tertiary)",
                  color: "var(--text-primary)",
                }}
              />
            </div>
            <div style={{ flex: "1 1 45%", display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>Repo Filter</span>
              <input
                type="text"
                placeholder="All repos"
                value={repoFilter}
                onChange={(e) => setRepoFilter(e.target.value)}
                style={{
                  padding: "6px 10px",
                  fontSize: 13,
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: "var(--bg-tertiary)",
                  color: "var(--text-primary)",
                }}
              />
            </div>
          </div>

          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              fontSize: 13,
              color: "var(--text-primary)",
              cursor: "pointer",
            }}
          >
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
            />
            Dry-run preview mode
          </label>

          <TouchButton
            disabled={purging || staleData.filter(r => r.safe_to_cancel).length === 0}
            onClick={executePurge}
            variant={confirmPurge ? "danger" : "primary"}
            style={{ minHeight: 40, width: "100%" }}
          >
            {confirmPurge
              ? "Confirm Purge"
              : `Purge Stale Runs (${staleData.filter(r => r.safe_to_cancel).length} safe)`}
          </TouchButton>

          {confirmPurge && (
            <span style={{ color: "var(--accent-red)", fontSize: 11, textAlign: "center" }}>
              Confirm within 5s. This will cancel all eligible stale runs.
            </span>
          )}
        </div>
      )}

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
