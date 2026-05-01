/**
 * SubTabs – horizontal tab strip extracted from legacy/App.tsx (#403).
 *
 * Supports controlled (activeKey + onChange) and uncontrolled usage,
 * optional localStorage persistence via storageKey, and a right badge slot.
 */

import React from "react"
import { Badge } from "../primitives/Badge"

interface SubTab {
  key: string
  label: React.ReactNode
  badge?: React.ReactNode
  disabled?: boolean
}

interface SubTabsProps {
  tabs: SubTab[]
  activeKey?: string
  onChange?: (key: string) => void
  storageKey?: string
  className?: string
  rightBadge?: React.ReactNode
}

export function SubTabs({
  tabs,
  activeKey: controlledKey,
  onChange,
  storageKey,
  className,
  rightBadge,
}: SubTabsProps) {
  const initialKey = storageKey
    ? (localStorage.getItem(storageKey) ?? tabs[0]?.key)
    : tabs[0]?.key

  const [internalActive, setInternalActive] = React.useState<string | undefined>(initialKey)

  const activeKey = controlledKey !== undefined ? controlledKey : internalActive

  function handleChange(key: string) {
    if (controlledKey === undefined) {
      setInternalActive(key)
    }
    if (storageKey) {
      try {
        localStorage.setItem(storageKey, key)
      } catch (_e) {}
    }
    onChange?.(key)
  }

  return React.createElement(
    "div",
    { className: "subtabs" + (className ? " " + className : "") },
    React.createElement(
      "div",
      { className: "subtabs-strip" },
      tabs.map((tab) =>
        React.createElement(
          "button",
          {
            key: tab.key,
            className: "subtab" + (activeKey === tab.key ? " active" : ""),
            disabled: tab.disabled ?? false,
            onClick: () => {
              if (!tab.disabled) handleChange(tab.key)
            },
          },
          tab.label,
          tab.badge != null
            ? React.createElement(
                Badge,
                { tone: activeKey === tab.key ? "info" : "neutral", size: "sm" },
                tab.badge,
              )
            : null,
        ),
      ),
    ),
    rightBadge
      ? React.createElement("div", { className: "subtabs-right" }, rightBadge)
      : null,
  )
}

export default SubTabs
