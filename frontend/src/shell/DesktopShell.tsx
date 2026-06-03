/**
 * DesktopShell.tsx — the modern desktop application shell (issue #802, #796).
 *
 * Composes the three merged shell surfaces into one GitHub-style desktop
 * layout, all driven by the single nav registry (DRY):
 *   - the left `Sidebar` (#798) — full grouped navigation;
 *   - the slim `TopToolstrip` (#799) — most-frequent categories + More menu;
 *   - the `Tooltip` primitive (#801) on every nav item (inside those surfaces)
 *     and on every shell action button here.
 *
 * The page body (the legacy App rendered chromeless, or any page) is passed as
 * `children` and mounted in the `main` landmark — orthogonality: a failing page
 * is isolated to the main region and cannot remove the nav chrome.
 *
 * LoD: flat typed props only — `activeTabId`, `onSelect(tabId)`, and a flat
 * list of `ShellAction` records. The shell never reaches into page or registry
 * internals.
 */
import React from "react";
import { Sidebar } from "./Sidebar";
import { TopToolstrip } from "./TopToolstrip";
import { Tooltip } from "../primitives/Tooltip";

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
  /**
   * Optional persistent controls rendered in the topbar before the action
   * buttons — e.g. the global ActiveProviderControl (#811). Kept as an opaque
   * node so the shell stays orthogonal and does not reach into provider state.
   */
  headerExtra?: React.ReactNode;
  /**
   * Optional leading topbar node rendered before `headerExtra` — used for the
   * Help/About '?' surface (#822). Opaque so the shell stays orthogonal.
   */
  helpAbout?: React.ReactNode;
  /**
   * Optional per-tab intro header (#822) rendered once at the top of the page
   * body, above `children`. Opaque node so the shell does not reach into the
   * nav registry or page state.
   */
  intro?: React.ReactNode;
  /** The page body for the active category. */
  children: React.ReactNode;
}

/**
 * Contract: every action MUST carry a non-empty tooltip (the a11y audit and the
 * shell both rely on it). A blank tooltip is a programming error — fail loudly.
 */
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

  return (
    <div className="desktop-shell">
      <Sidebar activeTabId={activeTabId} onSelect={onSelect} />
      <div className="desktop-shell__body">
        <header
          className="desktop-shell__topbar"
          role="banner"
        >
          <a className="skip-link" href="#main-content">
            Skip to main content
          </a>
          <TopToolstrip activeTabId={activeTabId} onSelect={onSelect} />
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
    </div>
  );
}
