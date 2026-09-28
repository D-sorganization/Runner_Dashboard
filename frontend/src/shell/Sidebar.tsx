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
import { Tooltip } from "../primitives/Tooltip";
import { ConnectionIndicator } from "../primitives/ConnectionIndicator";

function ProductMark({ size = 20 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 512 512"
      width={size}
      height={size}
      style={{ flexShrink: 0, borderRadius: "var(--radius-sm, 6px)" }}
    >
      <rect width="512" height="512" rx="96" fill="var(--bg-tertiary, #1c2333)" />
      <path d="M292 78 146 304h104l-24 130 140-222H258z" fill="var(--accent-blue, #58a6ff)" />
      <path d="M292 78 258 212h108L226 434l24-130H146z" fill="var(--accent-green, #3fb950)" opacity=".72" />
    </svg>
  );
}

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

  const nodeName =
    typeof window !== "undefined" && window.location.hostname
      ? window.location.hostname
      : "localhost";

  const renderItem = (item: NavItem) => {
    const isActive = item.tabId === activeTabId;
    const Icon = item.Icon;
    const itemBtn = (
      <button
        key={item.id}
        type="button"
        data-nav-item="true"
        title={railCollapsed ? undefined : item.tooltip}
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
          height: 32,
          minHeight: 32,
          boxSizing: "border-box",
          padding: railCollapsed ? 0 : "0 10px",
          justifyContent: railCollapsed ? "center" : "flex-start",
          margin: "1px 0",
          borderRadius: "var(--radius-sm, 6px)",
          border: "none",
          borderLeft: isActive
            ? "2px solid var(--accent-blue, #58a6ff)"
            : "2px solid transparent",
          background: isActive
            ? "var(--badge-info-bg, rgba(88, 166, 255, 0.15))"
            : "transparent",
          color: isActive
            ? "var(--text-primary, #e6edf3)"
            : "var(--text-secondary, #8b949e)",
          fontSize: "13.5px",
          fontWeight: isActive ? 600 : 400,
          cursor: "pointer",
          transition: "background 120ms ease, color 120ms ease",
        }}
      >
        <Icon />
        {!railCollapsed && (
          <span
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {item.label}
          </span>
        )}
      </button>
    );

    if (railCollapsed) {
      return (
        <Tooltip key={item.id} content={item.label} placement="right">
          {itemBtn}
        </Tooltip>
      );
    }
    return itemBtn;
  };

  return (
    <nav
      ref={navRef}
      aria-label="Dashboard sections"
      style={{
        width: railCollapsed ? 56 : 240,
        flex: "0 0 auto",
        height: "100%",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-secondary, #161b22)",
        borderRight: "1px solid var(--border, #30363d)",
        transition: "width 140ms ease",
        overflow: "hidden",
      }}
    >
      {/* Top Header: product mark, name, collapse button */}
      <div
        style={{
          flex: "0 0 auto",
          display: "flex",
          alignItems: "center",
          justifyContent: railCollapsed ? "center" : "space-between",
          padding: railCollapsed ? "10px 0 8px" : "12px 12px 10px",
          borderBottom: "1px solid var(--border, #30363d)",
          gap: 8,
          minHeight: 48,
          boxSizing: "border-box",
        }}
      >
        {!railCollapsed ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              minWidth: 0,
            }}
          >
            <ProductMark size={22} />
            <span
              style={{
                fontWeight: 600,
                fontSize: 14,
                color: "var(--text-primary, #e6edf3)",
                letterSpacing: "-0.01em",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              Runner Dashboard
            </span>
          </div>
        ) : (
          <ProductMark size={22} />
        )}
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
            borderRadius: "var(--radius-sm, 6px)",
            border: "1px solid var(--border, #30363d)",
            background: "var(--bg-primary, #0f1117)",
            color: "var(--text-secondary, #8b949e)",
            cursor: "pointer",
            flexShrink: 0,
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

      {/* Middle: Scrollable nav groups */}
      <div
        style={{
          flex: "1 1 auto",
          overflowY: "auto",
          overflowX: "hidden",
          padding: "8px 8px 16px",
        }}
      >
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
                    color: "var(--text-muted, #868e98)",
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: "0.06em",
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
      </div>

      {/* Footer: Node hostname + ConnectionIndicator */}
      <div
        data-testid="sidebar-footer"
        style={{
          flex: "0 0 auto",
          borderTop: "1px solid var(--border, #30363d)",
          padding: railCollapsed ? "8px 4px" : "8px 12px",
          display: "flex",
          flexDirection: railCollapsed ? "column" : "row",
          alignItems: "center",
          justifyContent: railCollapsed ? "center" : "space-between",
          gap: 6,
          background: "var(--bg-secondary, #161b22)",
          minHeight: 40,
          boxSizing: "border-box",
        }}
      >
        {!railCollapsed ? (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              minWidth: 0,
              flex: 1,
            }}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "var(--radius-pill, 9999px)",
                background: "var(--accent-green, #3fb950)",
                flexShrink: 0,
              }}
              aria-hidden="true"
            />
            <span
              style={{
                fontSize: 12,
                color: "var(--text-muted, #868e98)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontVariantNumeric: "tabular-nums",
              }}
              title={nodeName}
            >
              {nodeName}
            </span>
          </div>
        ) : (
          <Tooltip content={`Node: ${nodeName}`} placement="right">
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "var(--radius-pill, 9999px)",
                background: "var(--accent-green, #3fb950)",
                display: "inline-block",
                cursor: "pointer",
              }}
              aria-label={`Node: ${nodeName}`}
            />
          </Tooltip>
        )}
        <ConnectionIndicator />
      </div>
    </nav>
  );
}
