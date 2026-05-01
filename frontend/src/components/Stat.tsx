/**
 * Stat – single metric card extracted from legacy/App.tsx (#403).
 *
 * Renders a labelled value with an optional sub-line, used throughout
 * FleetTab, StatsTab, and PerformanceTab to display numeric KPIs.
 */

import React from "react"

interface StatProps {
  label: React.ReactNode
  value: React.ReactNode
  color?: string
  sub?: React.ReactNode
}

export function Stat({ label, value, color, sub }: StatProps) {
  return React.createElement(
    "div",
    { className: "stat-card" },
    React.createElement("div", { className: "stat-label" }, label),
    React.createElement(
      "div",
      { className: "stat-value", style: { color: color ?? "inherit" } },
      value,
    ),
    sub ? React.createElement("div", { className: "stat-sub" }, sub) : null,
  )
}

export default Stat
