/**
 * shellActions.ts — builders for the modern desktop shell's action bar
 * (extracted from the shell component so it can be unit-tested and so the
 * shell module only exports components, keeping React Fast Refresh happy).
 *
 * The actions are deliberately self-contained (no reach into the legacy App
 * internals) so the shell stays orthogonal and reversible: Refresh reloads
 * dashboard data, Login/Logout toggles the GitHub session, and "Classic
 * layout" pins the legacy shell via localStorage and reloads — the visible
 * escape hatch back to the old UI.
 */
import type { ShellAction } from "./DesktopShell"
import { LAYOUT_STORAGE_KEY } from "./layoutFlag"

/**
 * Build the modern desktop shell's action bar.
 *
 * `isLoggedIn` is passed in (derived from the reactive `useSession` hook,
 * issue #842) so the auth label flips correctly without a full page reload.
 * `onLoggedOut` lets the caller re-probe the session the moment logout
 * resolves, rather than relying on the reload.
 */
export function buildShellActions(
  isLoggedIn: boolean,
  onLoggedOut: () => void = () => window.location.reload(),
): ShellAction[] {
  return [
    {
      id: "refresh",
      label: "Refresh",
      tooltip:
        "Reload the dashboard to fetch the latest fleet, queue and run data.",
      onClick: () => window.location.reload(),
    },
    {
      id: "auth",
      label: isLoggedIn ? "Logout" : "Login",
      tooltip: isLoggedIn
        ? "Sign out of the dashboard GitHub session."
        : "Sign in with GitHub to enable runner and workflow controls.",
      onClick: () => {
        if (isLoggedIn) {
          fetch("/api/auth/logout", {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          })
            .then(() => onLoggedOut())
            .catch(() => onLoggedOut())
        } else {
          window.location.href = "/api/auth/github"
        }
      },
    },
    {
      id: "classic-layout",
      label: "Classic layout",
      tooltip:
        "Switch back to the legacy top-toolstrip layout (reversible; stored per browser).",
      onClick: () => {
        try {
          window.localStorage.setItem(LAYOUT_STORAGE_KEY, "legacy")
        } catch {
          /* storage unavailable — non-fatal */
        }
        window.location.reload()
      },
    },
  ]
}
