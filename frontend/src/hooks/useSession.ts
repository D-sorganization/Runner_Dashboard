/** useSession — identity from GET /api/auth/me (#3232, #842). */
import { useCallback, useEffect, useRef, useState } from "react";

export interface DashboardPrincipal {
  id: string;
  type: string;
  name: string;
  roles?: string[];
  scopes?: string[];
  github_username?: string | null;
}

export type AuthMethod = "github" | "tailscale" | "loopback";

interface SessionPresentation {
  authMethod: AuthMethod;
  displayName: string;
  signInLabel: string;
  loopbackHint: string | null;
}

const LOOPBACK_ADMIN_HINT =
  "Loopback access is development-only. Open the dashboard on its Tailscale URL for full admin.";

function authMethodForPrincipal(principal: DashboardPrincipal): AuthMethod {
  if (principal.id === "__loopback__") return "loopback";
  if (principal.id.startsWith("tailscale:")) return "tailscale";
  return "github";
}

function presentSession(principal: DashboardPrincipal): SessionPresentation {
  const authMethod = authMethodForPrincipal(principal);
  const tailscaleLogin =
    authMethod === "tailscale" ? principal.id.slice("tailscale:".length) : null;
  return {
    authMethod,
    displayName:
      authMethod === "github"
        ? (principal.github_username ?? principal.name ?? principal.id)
        : (tailscaleLogin ?? principal.name ?? principal.id),
    signInLabel:
      authMethod === "github"
        ? "Signed in with GitHub"
        : authMethod === "tailscale"
          ? "Signed in with Tailscale"
          : "Signed in via loopback (development)",
    loopbackHint: authMethod === "loopback" ? LOOPBACK_ADMIN_HINT : null,
  };
}

export type SessionProbeResult =
  | { loggedIn: false }
  | { loggedIn: true; principal: DashboardPrincipal; presentation: SessionPresentation };

export async function probeDashboardSession(
  fetchFn: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<SessionProbeResult> {
  const response = await fetchFn("/api/auth/me", {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    signal,
    headers: { "X-Requested-With": "XMLHttpRequest" },
  });
  if (response.status === 401 || !response.ok) return { loggedIn: false };
  const principal = (await response.json()) as DashboardPrincipal;
  return { loggedIn: true, principal, presentation: presentSession(principal) };
}

export const SESSION_POLL_MS = 5000;

export type SessionProbe = (
  fetchFn?: typeof fetch,
  signal?: AbortSignal,
) => Promise<SessionProbeResult>;

export interface SessionState {
  loading: boolean;
  loggedIn: boolean;
  authMethod?: AuthMethod;
  displayName?: string;
  signInLabel?: string;
  loopbackHint?: string | null;
  refresh: () => void;
}

export interface UseSessionOptions {
  probe?: SessionProbe;
  pollMs?: number;
}

function applyProbe(result: SessionProbeResult) {
  if (!result.loggedIn) {
    return {
      loggedIn: false as const,
      authMethod: undefined,
      displayName: undefined,
      signInLabel: undefined,
      loopbackHint: null,
    };
  }
  const { presentation } = result;
  return {
    loggedIn: true as const,
    authMethod: presentation.authMethod,
    displayName: presentation.displayName,
    signInLabel: presentation.signInLabel,
    loopbackHint: presentation.loopbackHint,
  };
}

export function useSession(options: UseSessionOptions = {}): SessionState {
  const probe = options.probe ?? probeDashboardSession;
  const pollMs = options.pollMs ?? SESSION_POLL_MS;
  const probeRef = useRef(probe);
  probeRef.current = probe;

  const [loading, setLoading] = useState(true);
  const [identity, setIdentity] = useState(() => applyProbe({ loggedIn: false }));

  const runProbe = useCallback(async () => {
    setIdentity(applyProbe(await probeRef.current()));
    setLoading(false);
  }, []);

  const refresh = useCallback(() => {
    void runProbe();
  }, [runProbe]);

  useEffect(() => {
    void runProbe();
    const onFocus = () => void runProbe();
    const onVisibility = () => {
      if (typeof document === "undefined" || document.visibilityState === "visible") {
        void runProbe();
      }
    };
    if (typeof window !== "undefined") {
      window.addEventListener("focus", onFocus);
      window.addEventListener("pageshow", onFocus);
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibility);
    }
    const timer = pollMs > 0 ? setInterval(() => void runProbe(), pollMs) : undefined;
    return () => {
      if (typeof window !== "undefined") {
        window.removeEventListener("focus", onFocus);
        window.removeEventListener("pageshow", onFocus);
      }
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibility);
      }
      if (timer) clearInterval(timer);
    };
  }, [runProbe, pollMs]);

  return { loading, ...identity, refresh };
}
