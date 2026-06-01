/**
 * TopToolstrip.tsx — the slim top navigation bar (issue #799, part of #796).
 *
 * Replaces the legacy ~24-button toolstrip with just the registry's
 * most-frequent categories as direct buttons; everything else collapses into a
 * grouped "More" dropdown. Rendered entirely from the nav registry (DRY):
 *  - `frequent: true` items → direct toolbar buttons, each wrapped in a
 *    `Tooltip` (hover + focus, aria-describedby);
 *  - the remaining categories → a single accessible `Dropdown` menu, ordered by
 *    nav group so related entries cluster.
 *
 * Active item is marked `aria-current="page"`. When the active tab is itself a
 * non-frequent category, the "More" trigger carries `aria-current` so the user
 * can still see where they are. LoD: the only inputs are the active tabId and an
 * `onSelect(tabId)` callback — the consumer never reaches into the registry.
 *
 * Responsive: the toolbar wraps (`flex-wrap`) and the labels hide on narrow
 * widths while icons remain (the `.slim-toolstrip__label` class is hidden below
 * a breakpoint by the shell stylesheet), so the strip degrades gracefully
 * rather than overflowing.
 *
 * Orthogonality: this is a pure presentational nav; it owns no page state, so a
 * failing page cannot break it.
 */
import React, { useMemo } from "react";
import { Tooltip } from "../primitives/Tooltip";
import { Dropdown, type DropdownItem } from "../primitives/Dropdown";
import {
  NAV_GROUPS,
  NAV_ITEMS,
  frequentItems,
  type NavItem,
} from "./navRegistry";

export interface TopToolstripProps {
  /** tabId of the currently-active category. */
  activeTabId: string;
  /** Called with a NavItem.tabId when the user selects a category. */
  onSelect: (tabId: string) => void;
}

export function TopToolstrip({
  activeTabId,
  onSelect,
}: TopToolstripProps): React.ReactElement {
  const frequent = useMemo(() => frequentItems(), []);

  // Non-frequent items, ordered by nav group so related entries cluster in the
  // overflow menu, flattened to the Dropdown's flat-item contract (LoD).
  const overflowItems: DropdownItem[] = useMemo(() => {
    const items: DropdownItem[] = [];
    for (const group of NAV_GROUPS) {
      for (const item of NAV_ITEMS) {
        if (item.group === group.id && !item.frequent) {
          items.push({
            id: item.id,
            label: item.label,
            Icon: item.Icon,
            active: item.tabId === activeTabId,
            onSelect: () => onSelect(item.tabId),
          });
        }
      }
    }
    return items;
  }, [activeTabId, onSelect]);

  // The active tab lives in the overflow menu iff it is not a frequent item.
  const moreActive = useMemo(
    () =>
      NAV_ITEMS.some((i) => !i.frequent && i.tabId === activeTabId),
    [activeTabId],
  );

  const renderFrequent = (item: NavItem) => {
    const isActive = item.tabId === activeTabId;
    const Icon = item.Icon;
    return (
      <Tooltip key={item.id} content={item.tooltip} placement="bottom">
        <button
          type="button"
          className="slim-toolstrip__btn"
          data-frequent="true"
          aria-current={isActive ? "page" : undefined}
          onClick={() => onSelect(item.tabId)}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            borderRadius: 6,
            border: "1px solid transparent",
            background: isActive ? "var(--bg-hover, #252d3a)" : "transparent",
            color: isActive
              ? "var(--text-primary, #e6edf3)"
              : "var(--text-secondary, #8b949e)",
            fontSize: 13,
            fontWeight: isActive ? 600 : 500,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          <Icon />
          <span className="slim-toolstrip__label">{item.label}</span>
        </button>
      </Tooltip>
    );
  };

  return (
    <div
      role="toolbar"
      aria-label="Primary navigation"
      className="slim-toolstrip"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        flexWrap: "wrap",
      }}
    >
      {frequent.map(renderFrequent)}
      <span style={{ flex: "0 0 auto" }}>
        <Dropdown
          label="More"
          items={overflowItems}
          triggerClassName="slim-toolstrip__more"
          triggerActive={moreActive}
        />
      </span>
    </div>
  );
}
