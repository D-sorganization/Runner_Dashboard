/**
 * SubTabs – horizontal tab strip extracted from legacy/App.tsx (#403).
 *
 * Supports controlled (activeKey + onChange) and uncontrolled usage,
 * optional localStorage persistence via storageKey, and a right badge slot.
 *
 * a11y (#833): the strip is an ARIA tablist with roving focus — only the
 * active tab is tabbable, and ←/→/↑/↓/Home/End move between enabled tabs
 * (WAI-ARIA tabs pattern). This mirrors the live legacy `App.tsx` copy that
 * this component supersedes (decomp pass 11, #836).
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
  /** Accessible name for the tablist (falls back to `label`, then a default). */
  ariaLabel?: string
  /** Alias for `ariaLabel`, preserved from the legacy call sites. */
  label?: string
}

export function SubTabs({
  tabs,
  activeKey: controlledKey,
  onChange,
  storageKey,
  className,
  rightBadge,
  ariaLabel,
  label,
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

  const enabledKeys = tabs.filter((t) => !t.disabled).map((t) => t.key)

  function onStripKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (enabledKeys.length === 0) return
    const idx = activeKey != null ? enabledKeys.indexOf(activeKey) : -1
    let next: string | null = null
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      next = enabledKeys[(idx + 1 + enabledKeys.length) % enabledKeys.length]
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      next = enabledKeys[(idx - 1 + enabledKeys.length) % enabledKeys.length]
    } else if (e.key === "Home") {
      next = enabledKeys[0]
    } else if (e.key === "End") {
      next = enabledKeys[enabledKeys.length - 1]
    }
    if (next != null) {
      e.preventDefault()
      handleChange(next)
    }
  }

  return React.createElement(
    "div",
    { className: "subtabs" + (className ? " " + className : "") },
    React.createElement(
      "div",
      {
        className: "subtabs-strip",
        role: "tablist",
        "aria-label": ariaLabel || label || "Section tabs",
        onKeyDown: onStripKeyDown,
      },
      tabs.map((tab) => {
        const selected = activeKey === tab.key
        return React.createElement(
          "button",
          {
            key: tab.key,
            className: "subtab" + (selected ? " active" : ""),
            role: "tab",
            "aria-selected": selected ? "true" : "false",
            "aria-disabled": tab.disabled ? "true" : undefined,
            tabIndex: selected ? 0 : -1,
            disabled: tab.disabled ?? false,
            onClick: () => {
              if (!tab.disabled) handleChange(tab.key)
            },
          },
          tab.label,
          tab.badge != null
            ? React.createElement(Badge, {
                tone: selected ? "info" : "neutral",
                size: "sm",
                children: tab.badge,
              })
            : null,
        )
      }),
    ),
    rightBadge
      ? React.createElement("div", { className: "subtabs-right" }, rightBadge)
      : null,
  )
}

export default SubTabs
