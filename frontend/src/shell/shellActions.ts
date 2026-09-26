/**
 * shellActions.ts — builders for the modern desktop shell's action bar
 * (extracted from the shell component so it can be unit-tested and so the
 * shell module only exports components, keeping React Fast Refresh happy).
 *
 * The actions are deliberately self-contained so the shell stays orthogonal:
 * Refresh reloads dashboard data and Login/Logout toggles the GitHub session.
 * The "Classic layout" action was retired with the legacy App (#1345).
 */
import type { ShellAction } from "./DesktopShell"

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
  ]
}
