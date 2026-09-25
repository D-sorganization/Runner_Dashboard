/**
 * Roster.tsx — Staff Roster Sidebar (SC-D3, Issue #1317).
 *
 * Top entry "Ask Barb (auto-route)", 4 operational tiers, search,
 * status dots, unread badges, pinning and keyboard navigation.
 */

import React, { useMemo, useState, useCallback, useRef } from "react";
import type { OperationalTier, RosterProps, RosterRole } from "./rosterTypes";
import { OPERATIONAL_TIERS } from "./rosterTypes";
import { RosterGroup } from "./RosterGroup";
import { RosterRow } from "./RosterRow";
import { resolveOperationalTier } from "./statusCalculator";
import "./Roster.css";

export const Roster: React.FC<RosterProps> = ({
  roles,
  selectedRole,
  onSelectRole,
  pinnedRoles,
  onTogglePin,
  isStale = false,
  staleReason = null,
  onRetry,
  isLoading = false,
  className = "",
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [focusedRole, setFocusedRole] = useState<string | null>(selectedRole || "auto");
  const containerRef = useRef<HTMLDivElement>(null);

  const toggleGroupCollapse = useCallback((tier: OperationalTier) => {
    setCollapsedGroups((prev) => ({
      ...prev,
      [tier]: !prev[tier],
    }));
  }, []);

  // Filter roles based on search query
  const filteredRoles = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return roles;
    return roles.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.title.toLowerCase().includes(q) ||
        (r.summary && r.summary.toLowerCase().includes(q))
    );
  }, [roles, searchQuery]);

  // Group filtered roles into operational tiers
  const groupedRoles = useMemo(() => {
    const map: Record<OperationalTier, RosterRole[]> = {
      Leadership: [],
      "Project Managers": [],
      Specialists: [],
      Operations: [],
    };
    for (const r of filteredRoles) {
      const tier = resolveOperationalTier(r.name, r.group);
      if (map[tier]) {
        map[tier].push(r);
      } else {
        map.Specialists.push(r);
      }
    }
    return map;
  }, [filteredRoles]);

  // Pinned roles list
  const pinnedList = useMemo(() => {
    if (!pinnedRoles.length) return [];
    return roles.filter((r) => pinnedRoles.includes(r.name));
  }, [roles, pinnedRoles]);

  // Flatten visible role names for keyboard navigation
  const navigableItems = useMemo(() => {
    const items: string[] = ["auto"];
    for (const r of pinnedList) {
      if (!items.includes(r.name)) items.push(r.name);
    }
    for (const tier of OPERATIONAL_TIERS) {
      if (!collapsedGroups[tier]) {
        for (const r of groupedRoles[tier]) {
          if (!items.includes(r.name)) items.push(r.name);
        }
      }
    }
    return items;
  }, [pinnedList, groupedRoles, collapsedGroups]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (navigableItems.length === 0) return;
      const currentIndex = focusedRole ? navigableItems.indexOf(focusedRole) : -1;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        const nextIndex = currentIndex < navigableItems.length - 1 ? currentIndex + 1 : 0;
        setFocusedRole(navigableItems[nextIndex]);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        const prevIndex = currentIndex > 0 ? currentIndex - 1 : navigableItems.length - 1;
        setFocusedRole(navigableItems[prevIndex]);
      } else if (e.key === "Enter") {
        if (focusedRole) {
          e.preventDefault();
          onSelectRole(focusedRole);
        }
      } else if (e.key === "Home") {
        e.preventDefault();
        setFocusedRole(navigableItems[0]);
      } else if (e.key === "End") {
        e.preventDefault();
        setFocusedRole(navigableItems[navigableItems.length - 1]);
      }
    },
    [navigableItems, focusedRole, onSelectRole]
  );

  return (
    <aside
      ref={containerRef}
      className={`staff-roster ${className}`}
      data-testid="staff-roster-container"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      role="region"
      aria-label="Staff Roster"
    >
      {/* Stale network / API warning banner */}
      {isStale && (
        <div className="roster-stale-banner" data-testid="roster-stale-banner" role="alert">
          <span className="roster-stale-banner__text">
            ⚠️ Stale roster · {staleReason || "Failed to update"}
          </span>
          {onRetry && (
            <button
              type="button"
              className="roster-stale-banner__retry"
              onClick={onRetry}
            >
              Retry
            </button>
          )}
        </div>
      )}

      {/* Top Search Filter */}
      <div className="roster-search">
        <input
          type="search"
          role="searchbox"
          className="roster-search__input"
          placeholder="Search roles, titles, mandates..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              setSearchQuery("");
            }
          }}
          aria-label="Search roles"
        />
        {searchQuery && (
          <button
            type="button"
            className="roster-search__clear"
            onClick={() => setSearchQuery("")}
            aria-label="Clear search"
          >
            ✕
          </button>
        )}
      </div>

      <div className="roster-list" role="listbox" aria-label="Staff roles list">
        {/* Top Entry: Ask Barb (auto-route) */}
        <div
          role="option"
          aria-selected={selectedRole === "auto"}
          data-testid="roster-auto-entry"
          className={`roster-auto-entry ${
            selectedRole === "auto" ? "roster-auto-entry--selected" : ""
          } ${focusedRole === "auto" ? "roster-auto-entry--focused" : ""}`}
          onClick={() => onSelectRole("auto")}
        >
          <div className="roster-auto-entry__avatar" aria-hidden="true">
            ✦
          </div>
          <div className="roster-auto-entry__content">
            <div className="roster-auto-entry__title-row">
              <span className="roster-auto-entry__name">Ask Barb</span>
              <span className="roster-auto-entry__badge">auto-route</span>
            </div>
            <p className="roster-auto-entry__desc">
              Personal Secretary & Attention Gate
            </p>
          </div>
        </div>

        {/* Pinned Section */}
        {pinnedList.length > 0 && !searchQuery && (
          <section className="roster-pinned" data-testid="roster-pinned-section">
            <div className="roster-pinned__header">
              <span>📌 PINNED</span>
            </div>
            <div className="roster-pinned__items">
              {pinnedList.map((role) => (
                <RosterRow
                  key={`pinned-${role.name}`}
                  role={role}
                  isSelected={selectedRole === role.name}
                  isFocused={focusedRole === role.name}
                  isPinned={true}
                  onSelect={() => onSelectRole(role.name)}
                  onTogglePin={() => onTogglePin(role.name)}
                  testIdSuffix="pinned"
                />
              ))}
            </div>
          </section>
        )}

        {/* 4 Operational Tiers */}
        {OPERATIONAL_TIERS.map((tier) => (
          <RosterGroup
            key={tier}
            tier={tier}
            roles={groupedRoles[tier]}
            selectedRole={selectedRole}
            focusedRole={focusedRole}
            pinnedRoles={pinnedRoles}
            isCollapsed={Boolean(collapsedGroups[tier])}
            onToggleCollapse={() => toggleGroupCollapse(tier)}
            onSelectRole={onSelectRole}
            onTogglePin={onTogglePin}
          />
        ))}

        {/* Empty Search State */}
        {filteredRoles.length === 0 && searchQuery && (
          <div className="roster-empty" role="status">
            <p>No roles matching "{searchQuery}"</p>
          </div>
        )}

        {/* Loading Indicator */}
        {isLoading && roles.length === 0 && (
          <div className="roster-loading" aria-busy="true">
            <p>Loading roster...</p>
          </div>
        )}
      </div>
    </aside>
  );
};
