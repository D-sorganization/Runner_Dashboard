/**
 * Roster.tsx — Staff Roster sidebar with grouped roles, Ask Barb entry,
 * live operational statuses, search filter, and pinning.
 *
 * Implements Workstream C (Issue #1721, Epic #1718).
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import type {
  RosterGroupKey,
  RosterProps,
  StaffRoleItem,
} from "./types";
import { ROSTER_GROUPS } from "./types";
import { categorizeRole, filterRoles } from "./rosterUtils";
import { RosterRow } from "./RosterRow";
import { RosterGroup } from "./RosterGroup";
import { NewPanelForm } from "./NewPanelForm";
import "./roster.css";

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
  onThreadCreated,
}) => {
  // ── New Panel Form State ───────────────────────────────────────────────────
  const [showPanelForm, setShowPanelForm] = useState(false);

  // ── Search State ────────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Global '/' shortcut to focus search input
  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const target = e.target as HTMLElement | null;
        const tagName = target?.tagName?.toLowerCase();
        const isEditable =
          target?.isContentEditable ||
          tagName === "input" ||
          tagName === "textarea" ||
          tagName === "select";
        if (!isEditable) {
          e.preventDefault();
          searchInputRef.current?.focus();
        }
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setSearchQuery("");
    } else if (e.key === "ArrowDown" && visibleNavRoles.length > 0) {
      e.preventDefault();
      setFocusedIndex(0);
    }
  };

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
      advisors: [],
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
    >
      {/* Sidebar Header */}
      <div className="roster-header">
        <div className="roster-header__title-wrap">
          <h2 className="roster-header__title">
            Staff
          </h2>
          {showStaleBadge && (
            <span
              data-testid="roster-stale-badge"
              title="Showing cached roster; network update failed"
              className="roster-stale-badge"
            >
              Stale Data
            </span>
          )}
        </div>

        <div className="roster-header__actions">
          <button
            type="button"
            className="roster-new-panel-btn"
            data-testid="roster-new-panel-button"
            aria-label="New panel"
            onClick={() => setShowPanelForm(true)}
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 16 16"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M8 2a.75.75 0 0 1 .75.75v4.5h4.5a.75.75 0 0 1 0 1.5h-4.5v4.5a.75.75 0 0 1-1.5 0v-4.5h-4.5a.75.75 0 0 1 0-1.5h4.5v-4.5A.75.75 0 0 1 8 2z" />
            </svg>
            <span>New panel</span>
          </button>
          {isLoading && (
            <span
              data-testid="roster-loading-indicator"
              className="roster-syncing-indicator"
            >
              Syncing...
            </span>
          )}
        </div>
      </div>

      {/* Search Input with search icon and shortcuts */}
      <div className="roster-search-container">
        <span className="roster-search-icon" aria-hidden="true">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor">
            <path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z" />
          </svg>
        </span>
        <input
          ref={searchInputRef}
          type="text"
          data-testid="roster-search-input"
          className="roster-search-input"
          placeholder="Filter roles or mandate..."
          value={searchQuery}
          onChange={(e) => {
            setSearchQuery(e.target.value);
            setFocusedIndex(-1);
          }}
          onKeyDown={handleSearchKeyDown}
          aria-label="Filter staff roles"
        />
        {searchQuery ? (
          <button
            type="button"
            data-testid="roster-search-clear"
            aria-label="Clear search"
            onClick={() => {
              setSearchQuery("");
              searchInputRef.current?.focus();
            }}
            className="roster-search-clear"
          >
            <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
              <path d="M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.75.75 0 1 1 1.06 1.06L9.06 8l3.22 3.22a.75.75 0 1 1-1.06 1.06L8 9.06l-3.22 3.22a.75.75 0 0 1-1.06-1.06L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06z" />
            </svg>
          </button>
        ) : (
          <kbd className="roster-search-shortcut" aria-hidden="true">/</kbd>
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
      <div style={{ marginBottom: "6px" }}>
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
      <div className="roster-divider" />

      {/* Empty Search Result */}
      {searchQuery && filteredRoles.length === 0 && (
        <div
          data-testid="roster-empty-search"
          className="roster-empty-search"
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

      {/* New Panel Dialog */}
      {showPanelForm && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="New expert panel"
          className="panel-dialog-overlay"
        >
          <div className="panel-dialog-content">
            <NewPanelForm
              onCreated={(thread) => {
                setShowPanelForm(false);
                onThreadCreated?.(thread);
              }}
              onCancel={() => setShowPanelForm(false)}
            />
          </div>
        </div>
      )}
    </aside>
  );
};

