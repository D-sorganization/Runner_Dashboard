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
import { Dropdown } from "../primitives/Dropdown";
import { CommandPalette, type Command } from "../primitives/CommandPalette";
import { NAV_ITEMS, NAV_GROUPS } from "./navRegistry";

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
      width="15"
      height="15"
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

function RefreshIcon({ className }: { className?: string }): React.ReactElement {
  return (
    <svg
      className={className}
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
    </svg>
  );
}

function UserAvatarIcon({ className }: { className?: string }): React.ReactElement {
  return (
    <svg
      className={className}
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function LoginIcon({ className }: { className?: string }): React.ReactElement {
  return (
    <svg
      className={className}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
      <polyline points="10 17 15 12 10 7" />
      <line x1="15" y1="12" x2="3" y2="12" />
    </svg>
  );
}

function LogoutIcon({ className }: { className?: string }): React.ReactElement {
  return (
    <svg
      className={className}
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
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

function UserMenuAction({ action }: { action: ShellAction }): React.ReactElement {
  const isLogout = action.label.toLowerCase().includes("out");
  return (
    <Dropdown
      id="shell-user-menu"
      data-testid="shell-user-menu"
      label={action.label}
      hideLabel={true}
      tooltip={action.tooltip}
      Icon={UserAvatarIcon}
      align="right"
      triggerClassName="shell-action shell-action--avatar"
      items={[
        {
          id: "auth",
          label: action.label,
          Icon: isLogout ? LogoutIcon : LoginIcon,
          onSelect: () => action.onClick(),
        },
      ]}
    />
  );
}

function ActionButton({ action }: { action: ShellAction }): React.ReactElement {
  if (action.id === "auth") {
    return <UserMenuAction action={action} />;
  }
  const Icon = action.Icon ?? (action.id === "refresh" ? RefreshIcon : undefined);
  return (
    <Tooltip content={action.tooltip} placement="bottom">
      <button
        type="button"
        className={`shell-action ${action.active ? "shell-action--active" : ""} ${Icon ? "shell-action--has-icon" : ""}`}
        aria-pressed={action.active ? true : undefined}
        aria-label={action.label}
        onClick={action.onClick}
      >
        {Icon ? <Icon className="shell-action__icon" /> : null}
        <span className="shell-action__label">{action.label}</span>
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

  const activeItem = NAV_ITEMS.find(
    (it) => it.tabId === activeTabId || it.id === activeTabId,
  );
  const activeGroup = activeItem
    ? NAV_GROUPS.find((g) => g.id === activeItem.group)
    : undefined;

  const commands: Command[] = useMemo(() => {
    return NAV_ITEMS.map((item) => ({
      id: `nav-${item.tabId}`,
      label: item.label,
      group: item.group.charAt(0).toUpperCase() + item.group.slice(1),
      action: () => onSelect(item.tabId),
      keywords: [item.id, item.tabId, item.group, item.tooltip],
      Icon: item.Icon,
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
          <div className="desktop-shell__title" data-testid="desktop-shell-title">
            {activeGroup ? (
              <>
                <span className="desktop-shell__breadcrumb-group">{activeGroup.label}</span>
                <span className="desktop-shell__breadcrumb-separator" aria-hidden="true">/</span>
              </>
            ) : null}
            <span className="desktop-shell__breadcrumb-item">{activeItem?.label ?? "Dashboard"}</span>
          </div>
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
                Search or jump to...
              </span>
              <kbd className="shell-search-kbd">⌘K / Ctrl K</kbd>
            </button>
          </div>
          <div className="desktop-shell__actions">
            {headerExtra}
            {helpAbout}
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
