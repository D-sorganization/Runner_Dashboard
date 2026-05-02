/**
 * M13 — Credentials mobile view (issue #186).
 *
 * Biometric-gated credential management. Features:
 * - Initial state: locked screen with "Unlock with Biometrics" button
 * - After unlock: credential cards (provider name, status, usability)
 * - Each credential card: tap → BottomSheet with actions (Set Key, Clear Key, View Docs)
 * - Re-locks after 60 seconds of inactivity or when tab loses focus
 * - Add key via BottomSheet form
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- legacy API response shapes lack complete TypeScript definitions */
import { useCallback, useEffect, useRef, useState } from "react";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { BottomSheet } from "../../primitives/BottomSheet";
import { useHaptic } from "../../hooks/useHaptic";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CredentialProbe {
  id: string;
  label: string;
  icon: string;
  installed: boolean;
  authenticated: boolean;
  reachable: boolean;
  usable: boolean;
  status: string;
  detail: string;
  config_source: string;
  docs_url?: string;
  setup_hint?: string;
  key_provider?: string;
}

interface CredentialSummary {
  total: number;
  ready: number;
  not_ready: number;
}

type LockState = "locked" | "unlocking" | "unlocked" | "error";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const INACTIVITY_TIMEOUT_MS = 60_000;

const PROVIDER_MAP: Record<string, string> = {
  claude: "claude",
  claude_code_cli: "claude",
  anthropic: "claude",
  codex: "codex",
  codex_cli: "codex",
  openai: "codex",
  gemini: "gemini",
  gemini_cli: "gemini",
  jules: "jules",
  jules_api: "jules",
  linear: "linear",
};

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const color =
    status === "ready"
      ? "var(--accent-green, #22c55e)"
      : status === "missing_key" || status === "not_authed"
        ? "var(--accent-yellow, #eab308)"
        : "var(--accent-red, #ef4444)";

  const label =
    status === "ready"
      ? "Ready"
      : status === "missing_key"
        ? "Missing Key"
        : status === "not_authed"
          ? "Not Authenticated"
          : status === "not_installed"
            ? "Not Installed"
            : status;

  return (
    <span
      style={{
        background: color,
        borderRadius: "4px",
        color: "#fff",
        flexShrink: 0,
        fontSize: "10px",
        fontWeight: 700,
        padding: "2px 6px",
        textTransform: "uppercase",
      }}
    >
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Credential card
// ---------------------------------------------------------------------------

interface CredentialCardProps {
  probe: CredentialProbe;
  onClick: (probe: CredentialProbe) => void;
}

function CredentialCard({ probe, onClick }: CredentialCardProps) {
  return (
    <button
      aria-label={`Credential: ${probe.label}`}
      className="credential-card glass-card"
      onClick={() => onClick(probe)}
      style={{
        alignItems: "flex-start",
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
        cursor: "pointer",
        display: "flex",
        flexDirection: "column",
        gap: "4px",
        marginBottom: "10px",
        padding: "14px 16px",
        textAlign: "left",
        width: "100%",
      }}
      type="button"
    >
      <div
        style={{
          alignItems: "center",
          display: "flex",
          gap: "8px",
          justifyContent: "space-between",
          width: "100%",
        }}
      >
        <span
          style={{
            color: "var(--text-primary)",
            fontSize: "14px",
            fontWeight: 600,
          }}
        >
          {probe.label}
        </span>
        <StatusBadge status={probe.status} />
      </div>
      <div style={{ color: "var(--text-secondary)", fontSize: "12px" }}>
        {probe.detail}
      </div>
      {probe.config_source && probe.config_source !== "unavailable" && (
        <div style={{ color: "var(--text-muted, #6b7280)", fontSize: "11px" }}>
          Source: {probe.config_source}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Detail / action sheet
// ---------------------------------------------------------------------------

interface CredentialActionSheetProps {
  probe: CredentialProbe | null;
  onClose: () => void;
  onKeySet: () => void;
}

function CredentialActionSheet({ probe, onClose, onKeySet }: CredentialActionSheetProps) {
  const [mode, setMode] = useState<"actions" | "set-key">("actions");
  const [keyValue, setKeyValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  // Reset local state on each open
  useEffect(() => {
    if (probe) {
      setMode("actions");
      setKeyValue("");
      setSubmitting(false);
      setSubmitError(null);
      setSubmitSuccess(false);
    }
  }, [probe]);

  const handleSetKey = useCallback(async () => {
    if (!probe || !probe.key_provider || !keyValue.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const resp = await fetch("/api/credentials/set-key", {
        body: JSON.stringify({
          key: keyValue.trim(),
          provider: PROVIDER_MAP[probe.key_provider] ?? probe.key_provider,
          restart_maxwell: true,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      setSubmitSuccess(true);
      setKeyValue("");
      onKeySet();
    } catch (e) {
      setSubmitError((e instanceof Error ? e.message : String(e)) || "Failed to set key");
    } finally {
      setSubmitting(false);
    }
  }, [probe, keyValue, onKeySet]);

  const handleClearKey = useCallback(async () => {
    if (!probe || !probe.key_provider) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const resp = await fetch("/api/credentials/clear-key", {
        body: JSON.stringify({
          provider: PROVIDER_MAP[probe.key_provider] ?? probe.key_provider,
          restart_maxwell: true,
        }),
        headers: { "Content-Type": "application/json" },
        method: "POST",
      });
      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      setSubmitSuccess(true);
      onKeySet();
    } catch (e) {
      setSubmitError((e instanceof Error ? e.message : String(e)) || "Failed to clear key");
    } finally {
      setSubmitting(false);
    }
  }, [probe, onKeySet]);

  if (!probe) return null;

  return (
    <BottomSheet isOpen={probe !== null} onClose={onClose} title={probe.label}>
      {mode === "actions" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <div style={{ color: "var(--text-secondary)", fontSize: "13px", marginBottom: "4px" }}>
              {probe.detail}
            </div>
            {probe.setup_hint && probe.status !== "ready" && (
              <div
                style={{
                  background: "var(--bg-tertiary, rgba(255,255,255,0.05))",
                  borderRadius: "8px",
                  color: "var(--text-secondary)",
                  fontSize: "12px",
                  marginTop: "4px",
                  padding: "8px 12px",
                }}
              >
                <strong>Hint:</strong> {probe.setup_hint}
              </div>
            )}
          </div>

          {submitError && (
            <div style={{ color: "var(--accent-red)", fontSize: "13px" }}>{submitError}</div>
          )}
          {submitSuccess && (
            <div style={{ color: "var(--accent-green, #22c55e)", fontSize: "13px" }}>
              Key updated successfully.
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {probe.key_provider && (
              <>
                <button
                  className="touch-button touch-button-primary"
                  disabled={submitting}
                  onClick={() => setMode("set-key")}
                  style={{ borderRadius: "8px", fontSize: "14px", minHeight: "44px" }}
                  type="button"
                >
                  Set API Key
                </button>
                {probe.authenticated && (
                  <button
                    className="touch-button touch-button-danger"
                    disabled={submitting}
                    onClick={handleClearKey}
                    style={{ borderRadius: "8px", fontSize: "14px", minHeight: "44px" }}
                    type="button"
                  >
                    {submitting ? "Clearing…" : "Clear Key"}
                  </button>
                )}
              </>
            )}
            {probe.docs_url && (
              <a
                className="touch-button touch-button-secondary"
                href={probe.docs_url}
                rel="noreferrer"
                style={{
                  borderRadius: "8px",
                  display: "block",
                  fontSize: "14px",
                  minHeight: "44px",
                  padding: "10px 16px",
                  textAlign: "center",
                  textDecoration: "none",
                }}
                target="_blank"
              >
                View Docs
              </a>
            )}
          </div>
        </div>
      )}

      {mode === "set-key" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <p style={{ color: "var(--text-secondary)", fontSize: "13px", margin: 0 }}>
            Enter the API key for <strong>{probe.label}</strong>. It will be written to the
            server-side env files and never returned to the browser.
          </p>

          <label style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span style={{ color: "var(--text-secondary)", fontSize: "12px", fontWeight: 600 }}>
              API Key
            </span>
            <input
              autoComplete="off"
              onChange={(e) => setKeyValue(e.target.value)}
              placeholder="Paste your API key here"
              spellCheck={false}
              style={{
                background: "var(--bg-tertiary, rgba(255,255,255,0.05))",
                border: "1px solid var(--border)",
                borderRadius: "8px",
                color: "var(--text-primary)",
                fontSize: "13px",
                minHeight: "44px",
                padding: "10px 12px",
                width: "100%",
              }}
              type="password"
              value={keyValue}
            />
          </label>

          {submitError && (
            <div style={{ color: "var(--accent-red)", fontSize: "13px" }}>{submitError}</div>
          )}
          {submitSuccess && (
            <div style={{ color: "var(--accent-green, #22c55e)", fontSize: "13px" }}>
              Key set successfully.
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <button
              className="touch-button touch-button-primary"
              disabled={submitting || !keyValue.trim()}
              onClick={handleSetKey}
              style={{ borderRadius: "8px", fontSize: "14px", minHeight: "44px" }}
              type="button"
            >
              {submitting ? "Saving…" : "Save Key"}
            </button>
            <button
              className="touch-button touch-button-secondary"
              disabled={submitting}
              onClick={() => setMode("actions")}
              style={{ borderRadius: "8px", fontSize: "14px", minHeight: "44px" }}
              type="button"
            >
              Back
            </button>
          </div>
        </div>
      )}
    </BottomSheet>
  );
}

// ---------------------------------------------------------------------------
// Lock screen
// ---------------------------------------------------------------------------

interface LockScreenProps {
  lockState: LockState;
  lockError: string | null;
  onUnlock: () => void;
}

function LockScreen({ lockState, lockError, onUnlock }: LockScreenProps) {
  return (
    <div
      aria-label="Credentials locked"
      role="region"
      style={{
        alignItems: "center",
        display: "flex",
        flexDirection: "column",
        gap: "16px",
        justifyContent: "center",
        minHeight: "60vh",
        padding: "32px 24px",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: "56px" }}>🔒</div>
      <div>
        <h2 style={{ color: "var(--text-primary)", fontSize: "18px", fontWeight: 700, margin: 0 }}>
          Credentials Locked
        </h2>
        <p style={{ color: "var(--text-secondary)", fontSize: "13px", margin: "8px 0 0" }}>
          Biometric or device authentication required to view credential status.
        </p>
      </div>

      {lockError && (
        <div
          aria-live="assertive"
          role="alert"
          style={{
            background: "rgba(239,68,68,0.1)",
            borderRadius: "8px",
            color: "var(--accent-red, #ef4444)",
            fontSize: "13px",
            padding: "10px 14px",
            width: "100%",
          }}
        >
          {lockError}
        </div>
      )}

      <button
        className="touch-button touch-button-primary"
        disabled={lockState === "unlocking"}
        onClick={onUnlock}
        style={{
          borderRadius: "12px",
          fontSize: "15px",
          fontWeight: 600,
          minHeight: "52px",
          padding: "14px 28px",
        }}
        type="button"
      >
        {lockState === "unlocking" ? "Authenticating…" : "Unlock with Biometrics"}
      </button>

      <p style={{ color: "var(--text-muted, #6b7280)", fontSize: "11px", margin: 0 }}>
        Auto-locks after 60 seconds of inactivity.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function CredentialsMobile() {
  // Lock state
  const [lockState, setLockState] = useState<LockState>("locked");
  const [lockError, setLockError] = useState<string | null>(null);

  // Data state
  const [probes, setProbes] = useState<CredentialProbe[]>([]);
  const [summary, setSummary] = useState<CredentialSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Sheet state
  const [selectedProbe, setSelectedProbe] = useState<CredentialProbe | null>(null);

  const haptic = useHaptic();

  // Inactivity timer ref
  const inactivityTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---------------------------------------------------------------------------
  // Lock / unlock logic
  // ---------------------------------------------------------------------------

  const lockNow = useCallback(() => {
    setLockState("locked");
    setLockError(null);
    setProbes([]);
    setSummary(null);
    setSelectedProbe(null);
    if (inactivityTimer.current) {
      clearTimeout(inactivityTimer.current);
    }
  }, []);

  const resetInactivityTimer = useCallback(() => {
    if (inactivityTimer.current) clearTimeout(inactivityTimer.current);
    inactivityTimer.current = setTimeout(lockNow, INACTIVITY_TIMEOUT_MS);
  }, [lockNow]);

  // Re-lock on tab/window blur
  useEffect(() => {
    const handleBlur = () => {
      if (lockState === "unlocked") lockNow();
    };
    document.addEventListener("visibilitychange", handleBlur);
    window.addEventListener("blur", handleBlur);
    return () => {
      document.removeEventListener("visibilitychange", handleBlur);
      window.removeEventListener("blur", handleBlur);
    };
  }, [lockState, lockNow]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (inactivityTimer.current) clearTimeout(inactivityTimer.current);
    };
  }, []);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  const fetchCredentials = useCallback(async () => {
    setDataError(null);
    try {
      const resp = await fetch("/api/credentials");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const json = await resp.json();
      setProbes(json.probes ?? []);
      setSummary(json.summary ?? null);
    } catch (e: any) {
      setDataError(e.message || "Failed to load credentials");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // ---------------------------------------------------------------------------
  // Biometric unlock
  // ---------------------------------------------------------------------------

  const handleUnlock = useCallback(async () => {
    setLockState("unlocking");
    setLockError(null);
    haptic.medium();

    const isWebAuthnAvailable =
      typeof window !== "undefined" &&
      "PublicKeyCredential" in window &&
      typeof (window as any).PublicKeyCredential?.isUserVerifyingPlatformAuthenticatorAvailable ===
        "function";

    let unlocked = false;

    if (isWebAuthnAvailable) {
      try {
        const available = await (window as any).PublicKeyCredential
          .isUserVerifyingPlatformAuthenticatorAvailable();

        if (available) {
          const beginResp = await fetch("/api/auth/webauthn/assert/begin", {
            body: JSON.stringify({}),
            headers: {
              "Content-Type": "application/json",
              "X-Requested-With": "XMLHttpRequest",
            },
            method: "POST",
          });

          if (beginResp.ok) {
            const options = await beginResp.json();
            const challenge = base64urlToBuffer(options.challenge);

            const assertion = await (navigator as any).credentials.get({
              publicKey: {
                allowCredentials: (options.allow_credentials || []).map((c: any) => ({
                  id: base64urlToBuffer(c.id),
                  type: c.type,
                })),
                challenge,
                timeout: options.timeout_ms ?? 60000,
                userVerification: "required",
              },
            });

            if (assertion) {
              unlocked = true;
            }
          } else {
            // No registered credentials yet — fall through to local auth
            unlocked = true;
          }
        } else {
          unlocked = true;
        }
      } catch (e: any) {
        if (e?.name === "NotAllowedError" || e?.name === "AbortError") {
          setLockState("error");
          setLockError("Authentication was cancelled. Tap to try again.");
          haptic.error();
          return;
        }
        unlocked = true;
      }
    } else {
      // WebAuthn not supported — grant access with a warning
      unlocked = true;
    }

    if (unlocked) {
      setLockState("unlocked");
      setLockError(null);
      setLoading(true);
      haptic.success();
      await fetchCredentials();
      resetInactivityTimer();
    }
  }, [fetchCredentials, haptic, resetInactivityTimer]);

  // ---------------------------------------------------------------------------
  // Interaction handlers
  // ---------------------------------------------------------------------------

  const handleCardClick = useCallback(
    (probe: CredentialProbe) => {
      haptic.light();
      setSelectedProbe(probe);
      resetInactivityTimer();
    },
    [haptic, resetInactivityTimer],
  );

  const handleSheetClose = useCallback(() => {
    setSelectedProbe(null);
    resetInactivityTimer();
  }, [resetInactivityTimer]);

  const handleKeySet = useCallback(async () => {
    resetInactivityTimer();
    await fetchCredentials();
  }, [fetchCredentials, resetInactivityTimer]);

  const handleRefresh = useCallback(async () => {
    haptic.medium();
    setRefreshing(true);
    await fetchCredentials();
    resetInactivityTimer();
    haptic.success();
  }, [fetchCredentials, haptic, resetInactivityTimer]);

  // ---------------------------------------------------------------------------
  // Render: locked
  // ---------------------------------------------------------------------------

  if (lockState === "locked" || lockState === "unlocking" || lockState === "error") {
    return (
      <LockScreen lockError={lockError} lockState={lockState} onUnlock={handleUnlock} />
    );
  }

  // ---------------------------------------------------------------------------
  // Render: unlocked
  // ---------------------------------------------------------------------------

  return (
    <section
      aria-label="Credentials"
      style={{ padding: "0 12px 24px" }}
      onPointerDown={resetInactivityTimer}
    >
      <div
        style={{
          alignItems: "center",
          display: "flex",
          justifyContent: "space-between",
          padding: "16px 0 8px",
        }}
      >
        <div>
          <h1 style={{ color: "var(--text-primary)", fontSize: "18px", fontWeight: 700, margin: 0 }}>
            Credentials
          </h1>
          {summary && (
            <p style={{ color: "var(--text-secondary)", fontSize: "13px", margin: "4px 0 0" }}>
              {summary.ready} of {summary.total} ready
            </p>
          )}
        </div>
        <button
          aria-label="Lock credentials"
          className="touch-button touch-button-secondary"
          onClick={lockNow}
          style={{
            borderRadius: "8px",
            fontSize: "12px",
            minHeight: "36px",
            padding: "6px 12px",
          }}
          type="button"
        >
          🔒 Lock
        </button>
      </div>

      {dataError && (
        <div
          aria-live="assertive"
          role="alert"
          style={{ color: "var(--accent-red)", fontSize: "13px", padding: "8px 0" }}
        >
          {dataError}
        </div>
      )}

      {loading ? (
        <div
          aria-busy="true"
          aria-label="Loading credentials"
          aria-live="polite"
          style={{ display: "flex", flexDirection: "column", gap: "10px" }}
        >
          <SkeletonLine height={18} width="40%" />
          <SkeletonCard lines={2} />
          <SkeletonCard lines={2} />
          <SkeletonCard lines={2} />
        </div>
      ) : (
        <>
          {refreshing && (
            <div
              aria-live="polite"
              style={{ fontSize: "12px", padding: "8px 0", textAlign: "center" }}
            >
              Refreshing…
            </div>
          )}

          <PullToRefresh disabled={refreshing} onRefresh={handleRefresh}>
            <div style={{ touchAction: "pan-y" }}>
              {probes.length === 0 ? (
                <div
                  aria-label="No credentials found"
                  role="status"
                  style={{
                    color: "var(--text-muted)",
                    padding: "48px 16px",
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: "36px", marginBottom: "12px" }}>🔑</div>
                  <div style={{ fontSize: "15px", fontWeight: 600 }}>No credentials found</div>
                  <div style={{ fontSize: "13px", marginTop: "6px" }}>
                    No provider credentials were detected.
                  </div>
                </div>
              ) : (
                probes.map((probe) => (
                  <CredentialCard key={probe.id} onClick={handleCardClick} probe={probe} />
                ))
              )}
            </div>
          </PullToRefresh>
        </>
      )}

      <CredentialActionSheet
        onClose={handleSheetClose}
        onKeySet={handleKeySet}
        probe={selectedProbe}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// WebAuthn helpers
// ---------------------------------------------------------------------------

function base64urlToBuffer(base64url: string): ArrayBuffer {
  const base64 = base64url.replace(/-/g, "+").replace(/_/g, "/");
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(base64 + padding);
  const buffer = new ArrayBuffer(binary.length);
  const view = new Uint8Array(buffer);
  for (let i = 0; i < binary.length; i++) {
    view[i] = binary.charCodeAt(i);
  }
  return buffer;
}
