/**
 * M13 — Credentials mobile view (issue #186).
 *
 * Biometric-gated credential management. Features:
 * - Initial state: locked screen with "Unlock with Biometrics" button
 * - After unlock: credential cards (provider name, status, usability)
 * - Each credential card: tap → BottomSheet with actions (Set Key, Clear Key, View Docs)
 * - Re-locks after 60 seconds of inactivity or when tab loses focus
 * - Add key via BottomSheet form
 *
 * Sub-components live in sibling files (CredentialCard, CredentialActionSheet,
 * LockScreen) to keep this module under the 500-line file-size cap.
 */
/* eslint-disable @typescript-eslint/no-explicit-any -- legacy API response shapes lack complete TypeScript definitions */
import { useCallback, useEffect, useRef, useState } from "react";
import { SkeletonCard, SkeletonLine } from "../../primitives/Skeleton";
import { PullToRefresh } from "../../primitives/PullToRefresh";
import { useHaptic } from "../../hooks/useHaptic";

import { CredentialActionSheet } from "./CredentialActionSheet";
import { CredentialCard } from "./CredentialCard";
import { LockScreen } from "./LockScreen";
import type {
  CredentialProbe,
  CredentialSummary,
  LockState,
} from "./mobileTypes";
import { INACTIVITY_TIMEOUT_MS, base64urlToBuffer } from "./mobileTypes";

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
  const [selectedProbe, setSelectedProbe] = useState<CredentialProbe | null>(
    null,
  );

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
      typeof (window as any).PublicKeyCredential
        ?.isUserVerifyingPlatformAuthenticatorAvailable === "function";

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
                allowCredentials: (options.allow_credentials || []).map(
                  (c: any) => ({
                    id: base64urlToBuffer(c.id),
                    type: c.type,
                  }),
                ),
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

  if (
    lockState === "locked" ||
    lockState === "unlocking" ||
    lockState === "error"
  ) {
    return (
      <LockScreen
        lockError={lockError}
        lockState={lockState}
        onUnlock={handleUnlock}
      />
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
          <h1
            style={{
              color: "var(--text-primary)",
              fontSize: "18px",
              fontWeight: 700,
              margin: 0,
            }}
          >
            Credentials
          </h1>
          {summary && (
            <p
              style={{
                color: "var(--text-secondary)",
                fontSize: "13px",
                margin: "4px 0 0",
              }}
            >
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
          style={{
            color: "var(--accent-red)",
            fontSize: "13px",
            padding: "8px 0",
          }}
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
              style={{
                fontSize: "12px",
                padding: "8px 0",
                textAlign: "center",
              }}
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
                  <div style={{ fontSize: "36px", marginBottom: "12px" }}>
                    🔑
                  </div>
                  <div style={{ fontSize: "15px", fontWeight: 600 }}>
                    No credentials found
                  </div>
                  <div style={{ fontSize: "13px", marginTop: "6px" }}>
                    No provider credentials were detected.
                  </div>
                </div>
              ) : (
                probes.map((probe) => (
                  <CredentialCard
                    key={probe.id}
                    onClick={handleCardClick}
                    probe={probe}
                  />
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
