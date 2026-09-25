/**
 * DesktopShell.tsx — modern desktop application shell (SC-D2 / issue #1309).
 *
 * Composes the shell surfaces into the 4-area desktop layout:
 *   - left Sidebar: full 4-area navigation (Staff, Work, Fleet, Settings);
 *   - topbar: global search and command palette (Ctrl+K / Cmd+K) replacing
 *     the old toolstrip;
 *   - persistent controls: ThemeSelector, ActiveProviderControl, actions;
 *   - main region: page body or NotFoundPanel.
 *
 * LoD: flat typed props only — activeTabId, onSelect(tabId), and actions.
 */
import React, { useMemo, useState } from "react";
import { Sidebar } from "./Sidebar";
import { Tooltip } from "../primitives/Tooltip";
import { CommandPalette, type Command } from "../primitives/CommandPalette";
import { NAV_ITEMS } from "./navRegistry";

export interface ShellAction {
  /** Stable identifier. */
  id: string;
  /** Visible/accessible label for the action button. */
  label: string;
  /** Required one-line tooltip/description (hover + focus, aria-describedby). */
  tooltip: string;
  /** Click handler. */
  onClick: () => void;
  /** Optional leading icon. */
  Icon?: (props: { className?: string }) => React.ReactElement;
  /** Optional active/toggled visual state. */
  active?: boolean;
}

export interface DesktopShellProps {
  /** tabId of the currently-active category. */
  activeTabId: string;
  /** Called with a NavItem.tabId when a category is selected in any surface. */
  onSelect: (tabId: string) => void;
  /** Flat list of shell action buttons (refresh, chat, login, …). */
  actions: ShellAction[];
  /** Optional persistent controls rendered before actions in the topbar. */
  headerExtra?: React.ReactNode;
  /** Optional leading topbar node (Help/About ?). */
  helpAbout?: React.ReactNode;
  /** Optional per-tab intro header. */
  intro?: React.ReactNode;
  /** The page body for the active category. */
  children: React.ReactNode;
}

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

function assertActions(actions: ShellAction[]): void {
  for (const a of actions) {
    if (!a.id || !a.label || !a.tooltip || a.tooltip.trim().length === 0) {
      throw new Error(
        `DesktopShell: action "${a.id || "?"}" must have id, label and a non-empty tooltip`,
      );
    }
  }
}

function ActionButton({ action }: { action: ShellAction }): React.ReactElement {
  const Icon = action.Icon;
  return (
    <Tooltip content={action.tooltip} placement="bottom">
      <button
        type="button"
        className={`shell-action ${action.active ? "shell-action--active" : ""}`}
        aria-pressed={action.active ? true : undefined}
        onClick={action.onClick}
      >
        {Icon ? <Icon /> : null}
        <span>{action.label}</span>
      </button>
    </Tooltip>
  );
}

export function DesktopShell({
  activeTabId,
  onSelect,
  actions,
  headerExtra,
  helpAbout,
  intro,
  children,
}: DesktopShellProps): React.ReactElement {
  assertActions(actions);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const commands: Command[] = useMemo(() => {
    return NAV_ITEMS.map((item) => ({
      id: `nav-${item.tabId}`,
      label: item.label,
      group: item.group.charAt(0).toUpperCase() + item.group.slice(1),
      action: () => onSelect(item.tabId),
      keywords: [item.id, item.tabId, item.group, item.tooltip],
    }));
  }, [onSelect]);

  return (
    <div className="desktop-shell">
      <Sidebar activeTabId={activeTabId} onSelect={onSelect} />
      <div className="desktop-shell__body">
        <header className="desktop-shell__topbar" role="banner">
          <a className="skip-link" href="#main-content">
            Skip to main content
          </a>
          <div className="desktop-shell__search" role="search">
            <button
              type="button"
              className="shell-search-btn"
              onClick={() => setPaletteOpen(true)}
              aria-label="Open command palette (Ctrl+K or Cmd+K)"
              title="Search commands, pages, and staff roles (Ctrl+K)"
            >
              <SearchIcon className="shell-search-icon" />
              <span className="shell-search-placeholder">
                Search commands, pages...
              </span>
              <kbd className="shell-search-kbd">Ctrl K</kbd>
            </button>
          </div>
          <div className="desktop-shell__actions">
            {helpAbout}
            {headerExtra}
            {actions.map((a) => (
              <ActionButton key={a.id} action={a} />
            ))}
          </div>
        </header>
        <main
          id="main-content"
          role="main"
          tabIndex={-1}
          className="desktop-shell__main"
        >
          {intro}
          {children}
        </main>
      </div>
      <CommandPalette
        commands={commands}
        isOpen={paletteOpen}
        onOpenChange={setPaletteOpen}
      />
    </div>
  );
}
