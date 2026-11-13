/** Desktop shell action bar (#3232 session-aware auth). */
import type { ShellAction } from "./DesktopShell";
import type { SessionState } from "../hooks/useSession";

export type ShellSessionInput = Pick<SessionState, "loading" | "loggedIn" | "authMethod">;

export function buildShellActions(
  session: ShellSessionInput,
  onLoggedOut: () => void = () => window.location.reload(),
  onLogoutFailed: () => void = () => {},
): ShellAction[] {
  const refresh: ShellAction = {
    id: "refresh",
    label: "Refresh",
    tooltip: "Reload the dashboard to fetch the latest fleet, queue and run data.",
    onClick: () => window.location.reload(),
  };
  const showAuth =
    !session.loading && (!session.loggedIn || session.authMethod === "github");
  if (!showAuth) return [refresh];

  const isLogout = session.loggedIn && session.authMethod === "github";
  return [
    refresh,
    {
      id: "auth",
      label: isLogout ? "Logout" : "Login",
      tooltip: isLogout
        ? "Sign out of the dashboard GitHub session."
        : "Sign in with GitHub to enable runner and workflow controls.",
      onClick: () => {
        if (isLogout) {
          fetch("/api/auth/logout", {
            method: "POST",
            headers: { "X-Requested-With": "XMLHttpRequest" },
          })
            .then((response) => (response.ok ? onLoggedOut() : onLogoutFailed()))
            .catch(() => onLogoutFailed());
        } else {
          window.location.href = "/api/auth/github";
        }
      },
    },
  ];
}
