/**
 * Sidebar.tsx — GitHub-style left navigation sidebar (issue #798, part of #796).
 *
 * Renders entirely from the nav registry (DRY): one collapsible section per
 * group, every category as a nav button. Features:
 *  - active highlighting via aria-current="page";
 *  - per-group collapse, persisted to localStorage;
 *  - whole-sidebar collapse to an icon rail, persisted;
 *  - roving keyboard navigation (ArrowUp/Down) across visible items;
 *  - an accessible title/tooltip on every item (the registry tooltip);
 *  - a navigation landmark with an aria-label.
 *
 * LoD: the only inputs are the active tabId and an onSelect(tabId) callback —
 * the consumer never reaches into the registry itself.
 *
 * Orthogonality: this is a pure presentational nav; it does not fetch or own
 * page state, so a failing page cannot break it.
 */
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  NAV_GROUPS,
  itemsByGroup,
  type NavItem,
} from "./navRegistry";

const COLLAPSED_GROUPS_KEY = "dashboard.sidebar.collapsedGroups";
const RAIL_COLLAPSED_KEY = "dashboard.sidebar.railCollapsed";

export interface SidebarProps {
  /** tabId of the currently-active category. */
  activeTabId: string;
  /** Called with a NavItem.tabId when the user selects a category. */
  onSelect: (tabId: string) => void;
}

function readCollapsedGroups(): Set<string> {
  try {
    const raw = window.localStorage.getItem(COLLAPSED_GROUPS_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? new Set(arr.map(String)) : new Set();
  } catch {
    return new Set();
  }
}

function readRailCollapsed(): boolean {
  try {
    return window.localStorage.getItem(RAIL_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

export function Sidebar({ activeTabId, onSelect }: SidebarProps): React.ReactElement {
  const grouped = useMemo(() => itemsByGroup(), []);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() =>
    readCollapsedGroups(),
  );
  const [railCollapsed, setRailCollapsed] = useState<boolean>(() =>
    readRailCollapsed(),
  );
  const navRef = useRef<HTMLElement>(null);

  // Persist collapsed groups.
  useEffect(() => {
    try {
      window.localStorage.setItem(
        COLLAPSED_GROUPS_KEY,
        JSON.stringify([...collapsedGroups]),
      );
    } catch {
      /* storage unavailable — non-fatal */
    }
  }, [collapsedGroups]);

  // Persist rail collapse.
  useEffect(() => {
    try {
      window.localStorage.setItem(RAIL_COLLAPSED_KEY, String(railCollapsed));
    } catch {
      /* non-fatal */
    }
  }, [railCollapsed]);

  const toggleGroup = useCallback((groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  // Roving keyboard navigation across all currently-visible nav items.
  const handleItemKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const nav = navRef.current;
      if (!nav) return;
      const items = Array.from(
        nav.querySelectorAll<HTMLButtonElement>('button[data-nav-item="true"]'),
      );
      const idx = items.indexOf(e.currentTarget);
      if (idx === -1) return;
      e.preventDefault();
      const delta = e.key === "ArrowDown" ? 1 : -1;
      const nextIdx = (idx + delta + items.length) % items.length;
      items[nextIdx]?.focus();
    },
    [],
  );

  const renderItem = (item: NavItem) => {
    const isActive = item.tabId === activeTabId;
    const Icon = item.Icon;
    return (
      <button
        key={item.id}
        type="button"
        data-nav-item="true"
        title={item.tooltip}
        aria-label={railCollapsed ? item.label : undefined}
        aria-current={isActive ? "page" : undefined}
        onClick={() => onSelect(item.tabId)}
        onKeyDown={handleItemKeyDown}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          width: "100%",
          textAlign: "left",
          padding: railCollapsed ? "8px 0" : "6px 10px",
          justifyContent: railCollapsed ? "center" : "flex-start",
          margin: "1px 0",
          borderRadius: 6,
          border: "none",
          borderLeft: isActive
            ? "2px solid var(--accent-blue, #58a6ff)"
            : "2px solid transparent",
          background: isActive ? "var(--bg-hover, #252d3a)" : "transparent",
          color: isActive
            ? "var(--text-primary, #e6edf3)"
            : "var(--text-secondary, #8b949e)",
          fontSize: 13,
          fontWeight: isActive ? 600 : 400,
          cursor: "pointer",
        }}
      >
        <Icon />
        {!railCollapsed && <span>{item.label}</span>}
      </button>
    );
  };

  return (
    <nav
      ref={navRef}
      aria-label="Dashboard sections"
      style={{
        width: railCollapsed ? 56 : 232,
        flex: "0 0 auto",
        height: "100%",
        boxSizing: "border-box",
        overflowY: "auto",
        background: "var(--bg-secondary, #161b22)",
        borderRight: "1px solid var(--border, #30363d)",
        padding: "8px 8px 16px",
        transition: "width 140ms ease",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: railCollapsed ? "center" : "flex-end",
          marginBottom: 8,
        }}
      >
        <button
          type="button"
          aria-label={railCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!railCollapsed}
          title={railCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={() => setRailCollapsed((v) => !v)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: 6,
            border: "1px solid var(--border, #30363d)",
            background: "var(--bg-primary, #0f1117)",
            color: "var(--text-secondary, #8b949e)",
            cursor: "pointer",
          }}
        >
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ transform: railCollapsed ? "rotate(180deg)" : undefined }}
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>
      </div>

      {NAV_GROUPS.map((group) => {
        const items = grouped[group.id] ?? [];
        const isCollapsed = collapsedGroups.has(group.id);
        return (
          <div key={group.id} style={{ marginBottom: railCollapsed ? 4 : 10 }}>
            {!railCollapsed && (
              <button
                type="button"
                aria-expanded={!isCollapsed}
                title={group.label}
                onClick={() => toggleGroup(group.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  width: "100%",
                  padding: "4px 8px",
                  border: "none",
                  background: "transparent",
                  color: "var(--text-muted, #7a838e)",
                  fontSize: 11,
                  fontWeight: 700,
                  letterSpacing: "0.04em",
                  textTransform: "uppercase",
                  cursor: "pointer",
                }}
              >
                <svg
                  aria-hidden="true"
                  viewBox="0 0 24 24"
                  width="12"
                  height="12"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{
                    transform: isCollapsed ? "rotate(-90deg)" : undefined,
                    transition: "transform 120ms",
                  }}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
                <span>{group.label}</span>
              </button>
            )}
            {/* In rail mode groups are always shown (icons only). When expanded,
                a collapsed group hides its items. */}
            {(railCollapsed || !isCollapsed) && (
              <div role="list" style={{ marginTop: 2 }}>
                {items.map(renderItem)}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );
}
