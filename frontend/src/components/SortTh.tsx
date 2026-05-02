/**
 * SortTh – sortable table-header cell extracted from legacy/App.tsx (#403).
 *
 * Renders a <th> with click/keyboard handling to cycle sort direction.
 */

import React from "react"

interface SortState {
  key: string
  dir: "asc" | "desc"
}

interface SortThProps {
  label: string
  sortKey: string
  sort?: SortState | null
  setSort: (next: SortState) => void
  thProps?: React.ThHTMLAttributes<HTMLTableCellElement>
}

function sortStateNext(current: SortState | null | undefined, key: string): SortState {
  if (current && current.key === key) {
    return { key, dir: current.dir === "asc" ? "desc" : "asc" }
  }
  return { key, dir: "asc" }
}

export function SortTh({ label, sortKey, sort, setSort, thProps }: SortThProps) {
  const active = !!sort && sort.key === sortKey
  const dir = active ? sort!.dir : ""

  const props: React.ThHTMLAttributes<HTMLTableCellElement> = {
    ...thProps,
    className:
      ((thProps?.className ?? "") + " sortable" + (active ? " active" : "")).trim(),
    role: "button",
    tabIndex: 0,
    "aria-sort": active ? (dir === "desc" ? "descending" : "ascending") : "none",
    title: `Sort by ${label}`,
    onClick: () => setSort(sortStateNext(sort, sortKey)),
    onKeyDown: (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault()
        setSort(sortStateNext(sort, sortKey))
      }
    },
  }

  return React.createElement(
    "th",
    props,
    React.createElement(
      "span",
      { className: "sort-heading" },
      label,
      React.createElement(
        "span",
        { className: "sort-indicator" },
        active ? (dir === "desc" ? "↓" : "↑") : "↕",
      ),
    ),
  )
}

export { sortStateNext }
export default SortTh
