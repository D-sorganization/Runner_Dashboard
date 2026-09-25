/**
 * Roster.tsx — Staff Roster sidebar with grouped roles, Ask Barb entry,
 * live operational statuses, search filter, and pinning.
 *
 * Implements SC-D3 (Issue #1317) under Epic SC-D (#1350).
 */
import React, { useMemo, useRef, useState } from "react";
import type {
  RosterGroupKey,
  RosterProps,
  StaffRoleItem,
} from "./types";
import { ROSTER_GROUPS } from "./types";
import { categorizeRole, filterRoles } from "./rosterUtils";
import { RosterRow } from "./RosterRow";
import { RosterGroup } from "./RosterGroup";

const PINNED_STORAGE_KEY = "staff-console:pinned-roles";
const COLLAPSED_STORAGE_KEY = "staff-console:collapsed-groups";

const DEFAULT_PINNED: string[] = [];

export const Roster: React.FC<RosterProps> = ({
  roles = [],
  selectedRoleId,
  onSelectRole = () => {},
  pinnedRoleIds: controlledPinned,
  onTogglePin: controlledTogglePin,
  availableProviders,
  isLoading = false,
  isError = false,
  errorMessage,
  isStale = false,
  className = "",
}) => {
  // ── Search State ────────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

  // ── Pinned Roles Persistence ────────────────────────────────────────────────
  const [localPinned, setLocalPinned] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem(PINNED_STORAGE_KEY);
      if (stored) {
        return JSON.parse(stored) as string[];
      }
    } catch {
      // ignore storage access errors
    }
    return DEFAULT_PINNED;
  });

  const pinned = controlledPinned ?? localPinned;

  const handleTogglePin = (roleId: string) => {
    if (controlledTogglePin) {
      controlledTogglePin(roleId);
      return;
    }
    setLocalPinned((prev) => {
      const next = prev.includes(roleId)
        ? prev.filter((id) => id !== roleId)
        : [...prev, roleId];
      try {
        localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore storage errors
      }
      return next;
    });
  };

  // ── Collapsed Groups State ──────────────────────────────────────────────────
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>(() => {
    try {
      const stored = localStorage.getItem(COLLAPSED_STORAGE_KEY);
      if (stored) {
        return JSON.parse(stored) as Record<string, boolean>;
      }
    } catch {
      // ignore
    }
    return {};
  });

  const handleToggleCollapse = (groupKey: RosterGroupKey) => {
    setCollapsedGroups((prev) => {
      const next = { ...prev, [groupKey]: !prev[groupKey] };
      try {
        localStorage.setItem(COLLAPSED_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore
      }
      return next;
    });
  };

  // ── Auto-route Entry (Barb) ─────────────────────────────────────────────────
  const barbRole = useMemo<StaffRoleItem>(() => {
    const found = roles.find((r) => r.name.toLowerCase() === "barb");
    if (found) {
      return {
        ...found,
        title: "Ask Barb (auto-route)",
        summary: found.summary || "Secretary & attention gate; routes to specialists.",
      };
    }
    return {
      name: "barb",
      title: "Ask Barb (auto-route)",
      summary: "Secretary & attention gate; routes to specialists.",
      group: "leadership",
      valid: true,
      dispatchable: true,
    };
  }, [roles]);

  // ── Filtered & Grouped Roles ────────────────────────────────────────────────
  const filteredRoles = useMemo(() => {
    return filterRoles(roles, searchQuery);
  }, [roles, searchQuery]);

  const groupedRoles = useMemo(() => {
    const groups: Record<RosterGroupKey, StaffRoleItem[]> = {
      pinned: [],
      leadership: [],
      project_managers: [],
      specialists: [],
      operations: [],
    };

    // Populate pinned
    if (pinned.length > 0) {
      for (const id of pinned) {
        const r = filteredRoles.find((item) => item.name === id);
        if (r && !groups.pinned.some((p) => p.name === r.name)) {
          groups.pinned.push(r);
        }
      }
    }

    // Populate categorical groups
    for (const role of filteredRoles) {
      if (role.name.toLowerCase() === "barb") {
        continue;
      }
      const cat = categorizeRole(role);
      groups[cat].push(role);
    }

    return groups;
  }, [filteredRoles, pinned]);

  // ── Keyboard Navigation ─────────────────────────────────────────────────────
  const visibleNavRoles = useMemo<StaffRoleItem[]>(() => {
    const items: StaffRoleItem[] = [barbRole];

    // If pinned not collapsed
    if (!collapsedGroups["pinned"] && groupedRoles.pinned.length > 0) {
      for (const r of groupedRoles.pinned) {
        if (!items.some((it) => it.name === r.name)) {
          items.push(r);
        }
      }
    }

    for (const grp of ROSTER_GROUPS) {
      if (!collapsedGroups[grp.key]) {
        for (const r of groupedRoles[grp.key]) {
          if (!items.some((it) => it.name === r.name)) {
            items.push(r);
          }
        }
      }
    }
    return items;
  }, [barbRole, collapsedGroups, groupedRoles]);

  const [focusedIndex, setFocusedIndex] = useState<number>(-1);

  const handleContainerKeyDown = (e: React.KeyboardEvent) => {
    if (e.target === searchInputRef.current) {
      if (e.key === "ArrowDown" && visibleNavRoles.length > 0) {
        e.preventDefault();
        setFocusedIndex(0);
      }
      return;
    }

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setFocusedIndex((prev) => (prev < visibleNavRoles.length - 1 ? prev + 1 : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setFocusedIndex((prev) => (prev > 0 ? prev - 1 : visibleNavRoles.length - 1));
    } else if ((e.key === "Enter" || e.key === " ") && focusedIndex >= 0) {
      e.preventDefault();
      const targetRole = visibleNavRoles[focusedIndex];
      if (targetRole) {
        onSelectRole(targetRole.name);
      }
    }
  };

  const focusedRoleId = focusedIndex >= 0 ? visibleNavRoles[focusedIndex]?.name : undefined;

  // ── Stale Data Indicator ────────────────────────────────────────────────────
  const showStaleBadge = isStale || (isError && roles.length > 0);

  return (
    <aside
      className={`staff-roster-sidebar ${className}`}
      data-testid="staff-roster-sidebar"
      tabIndex={0}
      onKeyDown={handleContainerKeyDown}
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        maxWidth: "320px",
        height: "100%",
        minHeight: "400px",
        backgroundColor: "var(--bg-secondary, #161b22)",
        borderRight: "1px solid var(--border, #30363d)",
        padding: "12px",
        boxSizing: "border-box",
        overflowY: "auto",
      }}
    >
      {/* Sidebar Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <h2
            style={{
              margin: 0,
              fontSize: "14px",
              fontWeight: 700,
              color: "var(--text-primary, #e6edf3)",
              letterSpacing: "0.02em",
            }}
          >
            Staff Roster
          </h2>
          {showStaleBadge && (
            <span
              data-testid="roster-stale-badge"
              title="Showing cached roster; network update failed"
              style={{
                fontSize: "10px",
                fontWeight: 600,
                padding: "2px 6px",
                borderRadius: "10px",
                backgroundColor: "var(--badge-warning-bg, rgba(210, 153, 34, 0.15))",
                color: "var(--badge-warning-fg, #d29922)",
                border: "1px solid rgba(210, 153, 34, 0.3)",
              }}
            >
              Stale Data
            </span>
          )}
        </div>

        {isLoading && (
          <span
            data-testid="roster-loading-indicator"
            style={{
              fontSize: "11px",
              color: "var(--text-secondary, #8b949e)",
            }}
          >
            Syncing...
          </span>
        )}
      </div>

      {/* Search Input */}
      <div style={{ position: "relative", marginBottom: "12px" }}>
        <input
          ref={searchInputRef}
          type="text"
          data-testid="roster-search-input"
          placeholder="Filter roles or mandate..."
          value={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value);
            setFocusedIndex(-1);
          }}
          aria-label="Filter staff roles"
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: "6px 28px 6px 10px",
            fontSize: "12px",
            backgroundColor: "var(--bg-card, #1c2128)",
            border: "1px solid var(--border, #30363d)",
            borderRadius: "6px",
            color: "var(--text-primary, #e6edf3)",
          }}
        />
        {searchQuery && (
          <button
            type="button"
            data-testid="roster-search-clear"
            aria-label="Clear search"
            onClick={() => {
              setSearchQuery("");
              searchInputRef.current?.focus();
            }}
            style={{
              position: "absolute",
              right: "6px",
              top: "50%",
              transform: "translateY(-50%)",
              background: "none",
              border: "none",
              color: "var(--text-muted, #868e98)",
              cursor: "pointer",
              fontSize: "12px",
              padding: "2px",
            }}
          >
            ×
          </button>
        )}
      </div>

      {/* Error state without any retained roster */}
      {isError && roles.length === 0 && (
        <div
          data-testid="roster-error-banner"
          style={{
            padding: "10px",
            borderRadius: "6px",
            backgroundColor: "var(--badge-danger-bg, rgba(248, 81, 73, 0.15))",
            color: "var(--badge-danger-fg, #f85149)",
            fontSize: "12px",
            marginBottom: "12px",
          }}
        >
          {errorMessage || "Failed to load staff roster. Please check network connection."}
        </div>
      )}

      {/* Top Entry: Ask Barb (auto-route) */}
      <div style={{ marginBottom: "10px" }}>
        <RosterRow
          role={barbRole}
          isSelected={selectedRoleId === barbRole.name || selectedRoleId === "auto"}
          isPinned={pinned.includes("barb")}
          isFocused={focusedRoleId === barbRole.name}
          onSelect={onSelectRole}
          onTogglePin={handleTogglePin}
          availableProviders={availableProviders}
          isAutoRoute={true}
        />
      </div>

      {/* Divider */}
      <div
        style={{
          height: "1px",
          backgroundColor: "var(--border, #30363d)",
          margin: "4px 0 10px 0",
        }}
      />

      {/* Empty Search Result */}
      {searchQuery && filteredRoles.length === 0 && (
        <div
          data-testid="roster-empty-search"
          style={{
            padding: "16px 8px",
            textAlign: "center",
            color: "var(--text-secondary, #8b949e)",
            fontSize: "12px",
          }}
        >
          No roles match &quot;{searchQuery}&quot;
        </div>
      )}

      {/* Pinned Group (if any pinned roles exist) */}
      {groupedRoles.pinned.length > 0 && (
        <RosterGroup
          groupKey="pinned"
          label="Pinned"
          roles={groupedRoles.pinned}
          isCollapsed={Boolean(collapsedGroups["pinned"])}
          onToggleCollapse={handleToggleCollapse}
          selectedRoleId={selectedRoleId}
          pinnedRoleIds={pinned}
          onSelectRole={onSelectRole}
          onTogglePin={handleTogglePin}
          availableProviders={availableProviders}
          focusedRoleId={focusedRoleId}
        />
      )}

      {/* 4 Categorical Groups from SC-D1 */}
      {ROSTER_GROUPS.map((grp) => (
        <RosterGroup
          key={grp.key}
          groupKey={grp.key}
          label={grp.label}
          roles={groupedRoles[grp.key]}
          isCollapsed={Boolean(collapsedGroups[grp.key])}
          onToggleCollapse={handleToggleCollapse}
          selectedRoleId={selectedRoleId}
          pinnedRoleIds={pinned}
          onSelectRole={onSelectRole}
          onTogglePin={handleTogglePin}
          availableProviders={availableProviders}
          focusedRoleId={focusedRoleId}
        />
      ))}
    </aside>
  );
};
