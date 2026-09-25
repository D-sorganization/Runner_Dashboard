/**
 * useRosterData.ts — Data hook aggregating roster, threads, and pins (SC-D3, Issue #1317).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchPins, pinRole as apiPinRole, unpinRole as apiUnpinRole } from "./pinsApi";
import type { RosterRole } from "./rosterTypes";
import { calculateRoleStatus, resolveOperationalTier } from "./statusCalculator";

interface ThreadSummary {
  id: string;
  role: string;
  unread_count: number;
  last_message?: {
    body_md: string;
    author: string;
    created_at: string;
  } | null;
}

interface RawThreadItem {
  id: string;
  participants?: string[];
  caller_unread_count?: number;
  unread_counters?: Record<string, number>;
  last_message?: {
    body_md: string;
    author: string;
    created_at: string;
  } | null;
}

interface RawRoleItem {
  name: string;
  title?: string;
  summary?: string;
  group?: string | null;
  providers?: string[];
  dispatchable?: boolean;
  valid?: boolean;
  errors?: string[];
  error?: string | null;
  active_runs?: number;
  budget?: {
    usd_per_run?: number | null;
    usd_per_day?: number | null;
  };
  holds?: string[];
  retired?: boolean;
}

export function useRosterData() {
  const [roles, setRoles] = useState<RosterRole[]>([]);
  const [pinnedRoles, setPinnedRoles] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isStale, setIsStale] = useState(false);
  const [staleReason, setStaleReason] = useState<string | null>(null);

  const lastKnownRolesRef = useRef<RosterRole[]>([]);

  const loadData = useCallback(async () => {
    try {
      // 1. Fetch roster and threads in parallel
      const [rosterRes, threadsRes, pinsList] = await Promise.all([
        fetch("/api/v1/staff/roster").catch(() => null),
        fetch("/api/v1/staff/threads?limit=100").catch(() => null),
        fetchPins(),
      ]);

      if (!rosterRes || !rosterRes.ok) {
        setIsStale(true);
        setStaleReason("Failed to connect to roster API");
        if (lastKnownRolesRef.current.length > 0) {
          setRoles(lastKnownRolesRef.current);
        }
        return;
      }

      const rosterData = await rosterRes.json();
      let threadsData: { items?: RawThreadItem[] } = {};
      if (threadsRes && threadsRes.ok) {
        threadsData = await threadsRes.json();
      }

      const threadMap: Record<string, ThreadSummary> = {};
      if (Array.isArray(threadsData.items)) {
        for (const t of threadsData.items) {
          const roleParticipant = (t.participants || []).find(
            (p: string) => !p.startsWith("principal:") && p !== "user"
          );
          if (roleParticipant) {
            const callerUnread = t.caller_unread_count || Object.values(t.unread_counters || {})[0] || 0;
            threadMap[roleParticipant] = {
              id: t.id,
              role: roleParticipant,
              unread_count: Number(callerUnread),
              last_message: t.last_message || null,
            };
          }
        }
      }

      const rawRoles: RawRoleItem[] = Array.isArray(rosterData?.roles) ? rosterData.roles : [];
      const installedProviders = rosterData?.providers || {};

      const mappedRoles: RosterRole[] = rawRoles.map((r: RawRoleItem) => {
        const thread = threadMap[r.name];
        const unreadCount = thread?.unread_count || 0;
        const lastMsg = thread?.last_message || null;

        const roleForStatus = {
          valid: r.valid,
          errors: r.errors,
          error: r.error,
          retired: r.retired,
          dispatchable: r.dispatchable,
          providers: r.providers,
          holds: r.holds,
          active_runs: r.active_runs,
          unread_count: unreadCount,
        };

        const { status, status_reason } = calculateRoleStatus(roleForStatus, installedProviders);

        return {
          name: r.name,
          title: r.title || r.name,
          summary: r.summary || "",
          group: resolveOperationalTier(r.name, r.group),
          providers: r.providers || [],
          dispatchable: r.dispatchable ?? true,
          valid: r.valid ?? true,
          errors: r.errors,
          error: r.error,
          active_runs: r.active_runs || 0,
          unread_count: unreadCount,
          status,
          status_reason,
          last_message: lastMsg,
          budget: r.budget,
          holds: r.holds,
          retired: r.retired,
        };
      });

      lastKnownRolesRef.current = mappedRoles;
      setRoles(mappedRoles);
      setPinnedRoles(pinsList);
      setIsStale(false);
      setStaleReason(null);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to update roster";
      setIsStale(true);
      setStaleReason(msg);
      if (lastKnownRolesRef.current.length > 0) {
        setRoles(lastKnownRolesRef.current);
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    // Poll every 10 seconds for real-time status updates
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, [loadData]);

  const togglePin = useCallback(
    async (roleName: string) => {
      const isPinned = pinnedRoles.includes(roleName);
      if (isPinned) {
        setPinnedRoles((prev) => prev.filter((r) => r !== roleName));
        const updated = await apiUnpinRole(roleName);
        setPinnedRoles(updated);
      } else {
        setPinnedRoles((prev) => [...prev, roleName]);
        const updated = await apiPinRole(roleName);
        setPinnedRoles(updated);
      }
    },
    [pinnedRoles]
  );

  return {
    roles,
    pinnedRoles,
    isLoading,
    isStale,
    staleReason,
    refetch: loadData,
    togglePin,
  };
}
