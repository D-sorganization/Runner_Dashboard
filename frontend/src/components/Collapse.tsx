/**
 * Collapse – collapsible section wrapper extracted from legacy/App.tsx (#403).
 *
 * Renders a header (with optional icon, title, badge) that toggles the
 * visibility of its children via a CSS class swap.
 */

import React from "react"
import { Badge } from "../primitives/Badge"

function ChevronDown() {
  return React.createElement(
    "svg",
    {
      width: 16,
      height: 16,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    },
    React.createElement("path", { d: "M6 9l6 6 6-6" }),
  )
}

interface CollapseProps {
  title: React.ReactNode
  icon?: React.ReactNode
  badge?: React.ReactNode
  defaultOpen?: boolean
  children?: React.ReactNode
}

export function Collapse({ title, icon, badge, defaultOpen = true, children }: CollapseProps) {
  const [open, setOpen] = React.useState(defaultOpen)

  return React.createElement(
    "div",
    { className: "section" },
    React.createElement(
      "div",
      {
        className: "section-header",
        onClick: () => setOpen((o) => !o),
      },
      React.createElement(
        "div",
        { className: "section-title" },
        icon,
        title,
        badge != null
          ? React.createElement(Badge, { tone: "neutral" }, badge)
          : null,
      ),
      React.createElement(
        "span",
        { className: "chevron" + (open ? " open" : "") },
        React.createElement(ChevronDown, null),
      ),
    ),
    React.createElement(
      "div",
      { className: "section-body" + (open ? "" : " collapsed") },
      children,
    ),
  )
}

export default Collapse
