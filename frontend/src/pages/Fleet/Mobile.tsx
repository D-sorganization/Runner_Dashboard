import { useCallback, useEffect, useMemo, useState } from "react";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { KpiHeader } from "./KpiHeader";
import { RunnerCard } from "./RunnerCard";
import { StatusPill } from "./StatusPill";

interface FleetNode {
  status?: string;
  hostname?: string;
  cpu_percent?: number;
  memory_percent?: number;
  uptime_seconds?: number;
  current_job?: string | null;
}

type FilterStatus = "all" | "online" | "busy" | "offline";

export function FleetMobile() {
  const [data, setData] = useState<Record<string, FleetNode>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterStatus>("all");
  const [refreshing, setRefreshing] = useState(false);

  const fetchFleet = useCallback(async () => {
    try {
      const resp = await fetch("/api/fleet/status");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      setData(json);
      setError(null);
    } catch (e: any) {
      setError(e.message || "Failed to load fleet data");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchFleet();
    const interval = setInterval(fetchFleet, 30000);
    return () => clearInterval(interval);
  }, [fetchFleet]);

  const nodes = useMemo(() => Object.entries(data), [data]);

  const { total, online, busy, offline } = useMemo(() => {
    let o = 0, b = 0, f = 0;
    for (const [, n] of nodes) {
      const s = n.status?.toLowerCase() || "";
      if (s === "online") o++;
      else if (s === "busy" || s === "running") b++;
      else f++;
    }
    return { total: nodes.length, online: o, busy: b, offline: f };
  }, [nodes]);

  const filtered = useMemo(() => {
    if (filter === "all") return nodes;
    return nodes.filter(([, n]) => {
      const s = n.status?.toLowerCase() || "";
      if (filter === "busy") return s === "busy" || s === "running";
      return s === filter;
    });
  }, [nodes, filter]);

  function onPullDown(e: React.TouchEvent) {
    const target = e.currentTarget as HTMLElement;
    const startY = e.touches[0].clientY;
    let moved = false;
    function onMove(ev: TouchEvent) {
      const dy = ev.touches[0].clientY - startY;
      if (dy > 60 && target.scrollTop <= 0 && !moved) {
        moved = true;
        setRefreshing(true);
        fetchFleet();
      }
    }
    function onEnd() {
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onEnd);
    }
    window.addEventListener("touchmove", onMove);
    window.addEventListener("touchend", onEnd);
  }

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading fleet"
        aria-live="polite"
        className="fleet-mobile-loading"
        style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "10px" }}
      >
        <SkeletonLine height={18} width="40%" />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  if (error && nodes.length === 0) {
    return (
      <div aria-live="assertive" className="fleet-mobile-error" role="alert" style={{ color: "var(--accent-red)", padding: "24px", textAlign: "center" }}>
        {error}
      </div>
    );
  }

  return (
    <section aria-label="Fleet" className="fleet-mobile" style={{ padding: "0 12px 24px" }}>
      <KpiHeader total={total} online={online} busy={busy} offline={offline} />
      <div
        className="status-pills"
        role="group"
        aria-label="Filter by status"
        style={{ display: "flex", gap: "6px", justifyContent: "center", marginBottom: "12px", overflowX: "auto", padding: "4px 0" }}
      >
        <StatusPill count={total} label="All" onClick={() => setFilter("all")} selected={filter === "all"} status="online" />
        <StatusPill count={online} label="Online" onClick={() => setFilter("online")} selected={filter === "online"} status="online" />
        <StatusPill count={busy} label="Busy" onClick={() => setFilter("busy")} selected={filter === "busy"} status="busy" />
        <StatusPill count={offline} label="Offline" onClick={() => setFilter("offline")} selected={filter === "offline"} status="offline" />
      </div>
      {refreshing && (
        <div aria-live="polite" style={{ fontSize: "12px", padding: "8px 0", textAlign: "center" }}>
          Refreshing…
        </div>
      )}
      <div
        className="fleet-list"
        onTouchStart={onPullDown}
        style={{ touchAction: "pan-y", WebkitOverflowScrolling: "touch" }}
      >
        {filtered.length === 0 ? (
          <div className="fleet-empty" style={{ color: "var(--text-muted)", padding: "32px", textAlign: "center" }}>
            No runners match the selected filter.
          </div>
        ) : (
          filtered.map(([name, node]) => {
            const s = node.status?.toLowerCase() || "offline";
            const status = s === "online" ? "online" : s === "busy" || s === "running" ? "busy" : "offline";
            return (
              <RunnerCard
                key={name}
                cpuPercent={node.cpu_percent ?? 0}
                currentJob={node.current_job}
                machine={node.hostname || name}
                name={name}
                ramPercent={node.memory_percent ?? 0}
                status={status}
                uptimeSeconds={node.uptime_seconds ?? 0}
              />
            );
          })
        )}
      </div>
    </section>
  );
}
