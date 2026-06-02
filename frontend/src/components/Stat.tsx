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
  /** Explicit tooltip for the sub-line; defaults to `sub` when it is a string. */
  subTitle?: string
}

export function Stat({ label, value, color, sub, subTitle }: StatProps) {
  return React.createElement(
    "div",
    { className: "stat-card" },
    React.createElement("div", { className: "stat-label" }, label),
    React.createElement(
      "div",
      { className: "stat-value", style: { color: color ?? "inherit" } },
      value,
    ),
    sub
      ? React.createElement(
          "div",
          {
            className: "stat-sub",
            // Hover for the full text when the sub line has been truncated.
            title: subTitle ?? (typeof sub === "string" ? sub : ""),
          },
          sub,
        )
      : null,
  )
}

export default Stat
