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

const VALUE_COLOR_CLASS: Record<string, string> = {
  "var(--accent-blue)": "stat-value--accent-blue",
  "var(--accent-green)": "stat-value--accent-green",
  "var(--accent-orange)": "stat-value--accent-orange",
  "var(--accent-red)": "stat-value--accent-red",
  "var(--accent-yellow)": "stat-value--accent-yellow",
}

function valueClassName(color?: string): string {
  return ["stat-value", color ? VALUE_COLOR_CLASS[color] : ""].filter(Boolean).join(" ")
}

export function Stat({ label, value, color, sub, subTitle }: StatProps) {
  return React.createElement(
    "div",
    { className: "stat-card" },
    React.createElement("div", { className: "stat-label" }, label),
    React.createElement(
      "div",
      { className: valueClassName(color) },
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
