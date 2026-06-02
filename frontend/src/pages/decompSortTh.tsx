/**
 * decompSortTh.tsx — the sortable `<th>` header cell shared by tabs extracted
 * from the legacy `App.tsx` monolith (decomposition #836).
 *
 * Faithful to the legacy a11y-#833 implementation: native columnheader role (so
 * `aria-sort` is permitted), interactivity via tabIndex + Enter/Space, and the
 * action announced through `aria-label`. This deliberately differs from the
 * older `components/SortTh.tsx` (which still uses `role="button"`); the extracted
 * tabs must match the live legacy render exactly.
 *
 * Pure sort logic (`sortRows`, `normalizeSortValue`, `sortStateNext`) lives in
 * the sibling `decompSort.ts`; this file exports only the component so it
 * satisfies `react-refresh/only-export-components`.
 */
import React from "react";
import { sortStateNext, type SortState } from "./decompSort";

export interface SortThProps {
  label: string;
  sortKey: string;
  sort?: SortState | null;
  setSort: (next: SortState) => void;
  thProps?: React.ThHTMLAttributes<HTMLTableCellElement>;
}

export function SortTh({
  label,
  sortKey,
  sort,
  setSort,
  thProps,
}: SortThProps): React.ReactElement {
  const active = !!sort && sort.key === sortKey;
  const dir = active ? sort!.dir : "";
  const props: React.ThHTMLAttributes<HTMLTableCellElement> = {
    ...(thProps || {}),
    className:
      ((thProps && thProps.className) || "") +
      " sortable" +
      (active ? " active" : ""),
    scope: (thProps && thProps.scope) || "col",
    tabIndex: 0,
    "aria-sort": active
      ? dir === "desc"
        ? "descending"
        : "ascending"
      : "none",
    "aria-label": "Sort by " + label,
    title: "Sort by " + label,
    onClick: () => setSort(sortStateNext(sort, sortKey)),
    onKeyDown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        setSort(sortStateNext(sort, sortKey));
      }
    },
  };
  return (
    <th {...props}>
      <span className="sort-heading">
        {label}
        <span className="sort-indicator">
          {active ? (dir === "desc" ? "↓" : "↑") : "↕"}
        </span>
      </span>
    </th>
  );
}

export default SortTh;
