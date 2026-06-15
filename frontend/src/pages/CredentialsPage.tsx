/**
 * CredentialsPage.tsx — the "Credentials" tab, extracted (behaviour-wise 1:1)
 * from the legacy `App.tsx` monolith as part of the decomposition epic (#836,
 * pass 6).
 *
 * Shows a per-provider credential probe grid: a summary stat row (ready /
 * not-ready / total), a re-probe button, and a card per provider with status
 * badge, detail/setup hints, a "Set API key" action, and docs link. On mobile
 * viewports the tab is gated behind a WebAuthn biometric assertion (lock screen
 * + 60-second auto-relock + a confirm sheet before any key mutation).
 *
 * `CredentialsPage` owns the probe fetch/set-key side effects for the routed
 * desktop shell. `CredentialsTab` stays presentational so legacy callers and
 * focused tests can keep passing explicit probe payloads.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { RefreshGlyph } from "./decompIcons";

// ── Types ──────────────────────────────────────────────────────────────────

export interface CredentialProbe {
  id?: string;
  name?: string;
  label?: string;
  status?: string;
  detail?: string;
  setup_hint?: string;
  usable?: boolean;
  key_provider?: string;
  docs_url?: string;
}

export interface CredentialSummary {
  ready?: number;
  not_ready?: number;
  total?: number;
}

export interface CredentialsProps {
  probes?: CredentialProbe[];
  summary?: CredentialSummary;
  loading?: boolean;
  error?: string;
  onRefresh?: () => void;
  onSetKey?: (probe: CredentialProbe) => void;
  mobile?: boolean;
}

const JSON_HEADERS = {
  "Content-Type": "application/json",
  "X-Requested-With": "XMLHttpRequest",
};

interface CredentialsPayload {
  probes?: CredentialProbe[];
  summary?: CredentialSummary;
}

function normalizeCredentialsPayload(payload: unknown): CredentialsPayload {
  if (!payload || typeof payload !== "object") return {};
  const data = payload as CredentialsPayload;
  return {
    probes: Array.isArray(data.probes) ? data.probes : [],
    summary:
      data.summary && typeof data.summary === "object" ? data.summary : {},
  };
}

export function CredentialsPage(): React.ReactElement {
  const [data, setData] = useState<CredentialsPayload>({
    probes: [],
    summary: {},
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | undefined>(undefined);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(undefined);
    legacyFetch("/api/credentials", { signal })
      .then((r) =>
        r.json().then((payload: unknown) => {
          if (!r.ok) throw new Error("credentials HTTP " + r.status);
          return payload;
        }),
      )
      .then((payload) => {
        setData(normalizeCredentialsPayload(payload));
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error ? err.message : "Failed to probe credentials.",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const setCredentialKey = useCallback(
    (probe: CredentialProbe) => {
      const provider = probe.key_provider;
      if (!provider) return;
      const label = probe.label || probe.name || provider;
      const keyValue = window.prompt("Enter API key for " + label);
      if (!keyValue) return;
      legacyFetch("/api/credentials/set-key", {
        method: "POST",
        headers: JSON_HEADERS,
        body: JSON.stringify({
          provider,
          key: keyValue,
          restart_maxwell: false,
        }),
      })
        .then((r) =>
          r.json().then((payload: unknown) => {
            if (!r.ok) {
              const detail =
                payload && typeof payload === "object"
                  ? (payload as { detail?: string }).detail
                  : undefined;
              throw new Error(detail || "HTTP " + r.status);
            }
            return payload;
          }),
        )
        .then(() => {
          refresh();
        })
        .catch((err: unknown) => {
          setError(err instanceof Error ? err.message : "Failed to save key.");
        });
    },
    [refresh],
  );

  return (
    <CredentialsTab
      probes={data.probes || []}
      summary={data.summary || {}}
      loading={loading}
      error={error}
      onRefresh={() => refresh()}
      onSetKey={setCredentialKey}
    />
  );
}

// ── WebAuthn helpers (1:1 legacy) ────────────────────────────────────────────

function base64UrlToBuffer(value: string): ArrayBuffer {
  let padded = value.replace(/-/g, "+").replace(/_/g, "/");
  padded += "=".repeat((4 - (padded.length % 4)) % 4);
  const raw = window.atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out.buffer;
}

function credentialToPayload(credential: PublicKeyCredential | null): unknown {
  if (!credential) return {};
  const response = credential.response as AuthenticatorAssertionResponse | undefined;
  return {
    id: credential.id,
    type: credential.type,
    rawId: credential.rawId ? Array.from(new Uint8Array(credential.rawId)) : [],
    response: response
      ? {
          authenticatorData: response.authenticatorData
            ? Array.from(new Uint8Array(response.authenticatorData))
            : [],
          clientDataJSON: response.clientDataJSON
            ? Array.from(new Uint8Array(response.clientDataJSON))
            : [],
          signature: response.signature
            ? Array.from(new Uint8Array(response.signature))
            : [],
          userHandle: response.userHandle
            ? Array.from(new Uint8Array(response.userHandle))
            : null,
        }
      : {},
  };
}

// ── Status presentation helpers (1:1 legacy) ─────────────────────────────────

function statusColor(status?: string): string {
  if (status === "ready") return "var(--accent-green)";
  if (status === "not_installed") return "var(--text-muted)";
  if (status === "missing_key" || status === "not_authed" || status === "missing_env")
    return "var(--accent-yellow)";
  return "var(--accent-red)";
}

function statusBg(status?: string): string {
  if (status === "ready") return "rgba(63,185,80,0.12)";
  if (status === "not_installed") return "rgba(139,148,158,0.12)";
  if (status === "missing_key" || status === "not_authed" || status === "missing_env")
    return "rgba(210,153,34,0.12)";
  return "rgba(248,81,73,0.12)";
}

function statusLabel(status?: string): string {
  const labels: Record<string, string> = {
    ready: "Ready",
    not_installed: "Not installed",
    missing_key: "Missing key",
    missing_env: "Missing key",
    not_authed: "Not authenticated",
    auth_failed: "Auth failed",
    probe_failed: "Probe failed",
  };
  return (status && labels[status]) || status || "";
}

export function CredentialsTab({
  probes,
  summary,
  loading,
  error,
  onRefresh,
  onSetKey,
  mobile,
}: CredentialsProps): React.ReactElement {
  const probeList = probes || [];
  const sum = summary || {};
  const [mobileUnlocked, setMobileUnlocked] = useState(false);
  const mobileUnlockedRef = useRef(false);
  const [mobileUnlockStatus, setMobileUnlockStatus] = useState<string | null>(null);
  const [mobileConfirmProbe, setMobileConfirmProbe] = useState<CredentialProbe | null>(null);

  function lockMobileCredentials(message?: string): void {
    mobileUnlockedRef.current = false;
    setMobileUnlocked(false);
    setMobileConfirmProbe(null);
    if (message) setMobileUnlockStatus(message);
  }

  function requestMobileCredentialUnlock(): void {
    setMobileUnlockStatus("Requesting biometric assertion...");
    if (!window.PublicKeyCredential || !navigator.credentials || !navigator.credentials.get) {
      lockMobileCredentials("This browser does not expose WebAuthn credentials.");
      return;
    }
    legacyFetch("/api/auth/webauthn/assert/begin", {
      method: "POST",
      headers: JSON_HEADERS,
      body: "{}",
    })
      .then((r) =>
        r.json().then((data: { detail?: string; challenge?: string; allow_credentials?: Array<{ id: string; type?: string }>; timeout_ms?: number }) => {
          if (!r.ok) throw new Error((data && data.detail) || "WebAuthn assertion failed to start");
          return data;
        }),
      )
      .then((data) =>
        navigator.credentials.get({
          publicKey: {
            challenge: base64UrlToBuffer(data.challenge || ""),
            allowCredentials: (data.allow_credentials || []).map((cred) => ({
              id: base64UrlToBuffer(cred.id),
              type: (cred.type || "public-key") as PublicKeyCredentialType,
            })),
            timeout: data.timeout_ms || 60000,
            userVerification: "required",
          },
        }),
      )
      .then((credential) =>
        legacyFetch("/api/auth/webauthn/assert/complete", {
          method: "POST",
          headers: JSON_HEADERS,
          body: JSON.stringify({ credential: credentialToPayload(credential as PublicKeyCredential | null) }),
        }).then((r) =>
          r.json().then((data: { detail?: string }) => {
            if (!r.ok) throw new Error((data && data.detail) || "WebAuthn assertion was not accepted");
            return data;
          }),
        ),
      )
      .then(() => {
        mobileUnlockedRef.current = true;
        setMobileUnlocked(true);
        setMobileUnlockStatus("Unlocked for 60 seconds.");
        if (onRefresh) onRefresh();
      })
      .catch((err: Error) => {
        lockMobileCredentials((err && err.message) || "Biometric assertion failed.");
      });
  }

  useEffect(() => {
    if (!mobile) return;
    function onVisibilityChange(): void {
      if (document.hidden && mobileUnlockedRef.current) {
        lockMobileCredentials("Credentials re-locked when the tab lost focus.");
      }
    }
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [mobile]);

  useEffect(() => {
    if (!mobile || !mobileUnlocked) return;
    const timer = window.setTimeout(() => {
      lockMobileCredentials("Credentials re-locked after 60 seconds.");
    }, 60000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [mobile, mobileUnlocked]);

  if (mobile && !mobileUnlocked) {
    return (
      <div className="mobile-credentials-lock">
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>Credentials locked</div>
        <div style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.45, marginBottom: 14 }}>
          Mobile access to credential metadata requires a fresh biometric assertion. Secret values are never shown.
        </div>
        <button className="btn" onClick={requestMobileCredentialUnlock}>
          Show credentials
        </button>
        {mobileUnlockStatus ? (
          <div style={{ marginTop: 12, color: "var(--text-muted)", fontSize: 12 }}>{mobileUnlockStatus}</div>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      <div className="stat-row">
        <Stat label="Ready" value={sum.ready || 0} sub="providers available" />
        <Stat label="Not ready" value={sum.not_ready || 0} sub="need setup" />
        <Stat label="Total" value={sum.total || probeList.length} sub="providers probed" />
      </div>
      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <button className="btn" onClick={onRefresh} disabled={loading} aria-label="Re-probe runner status">
          <RefreshGlyph size={12} />
          {loading ? "Probing..." : "Re-probe"}
        </button>
        <span style={{ fontSize: 12, color: "var(--text-muted)" }}>Probes run locally. No secrets are shown.</span>
      </div>
      {error ? (
        <div
          style={{
            padding: "10px 12px",
            borderRadius: 8,
            background: "rgba(248,81,73,0.12)",
            color: "var(--accent-red)",
            fontSize: 12,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      ) : null}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 12 }}>
        {probeList.map((probe) => (
          <div
            key={probe.id || probe.name}
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 16,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
              <strong>{probe.label || probe.name || probe.id || "Provider"}</strong>
              <span
                className="section-badge"
                style={{ background: statusBg(probe.status), color: statusColor(probe.status) }}
              >
                {statusLabel(probe.status)}
              </span>
            </div>
            {probe.detail ? (
              <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-muted)" }}>{probe.detail}</div>
            ) : null}
            {probe.setup_hint ? (
              <div
                style={{
                  marginTop: 8,
                  fontSize: 12,
                  color: probe.usable ? "var(--text-secondary)" : "var(--accent-yellow)",
                }}
              >
                {probe.setup_hint}
              </div>
            ) : null}
            <div style={{ marginTop: 12, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {probe.key_provider && onSetKey ? (
                <button
                  className="btn"
                  style={{ fontSize: 12, padding: "6px 10px" }}
                  onClick={() => {
                    if (mobile) {
                      setMobileConfirmProbe(probe);
                      return;
                    }
                    onSetKey(probe);
                  }}
                >
                  {probe.usable ? "Replace API key" : "Set API key"}
                </button>
              ) : null}
              {probe.docs_url ? (
                <a
                  href={probe.docs_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ fontSize: 12, color: "var(--text-secondary)" }}
                >
                  Docs ↗
                </a>
              ) : null}
            </div>
          </div>
        ))}
      </div>
      {mobile && mobileConfirmProbe ? (
        <div
          className="mobile-credentials-sheet"
          role="dialog"
          aria-modal="true"
          aria-label="Confirm mobile credential change"
          onClick={() => {
            setMobileConfirmProbe(null);
          }}
        >
          <div
            className="mobile-credentials-sheet-panel"
            onClick={(e) => {
              e.stopPropagation();
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>Confirm sensitive operation</div>
            <div style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.45, marginBottom: 14 }}>
              {"This will open the server-side key update flow for " +
                (mobileConfirmProbe.label ||
                  mobileConfirmProbe.name ||
                  mobileConfirmProbe.key_provider ||
                  "this provider") +
                ". Continue only if you intend to change credential state."}
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                className="btn"
                onClick={() => {
                  setMobileConfirmProbe(null);
                }}
              >
                Cancel
              </button>
              <button
                className="btn"
                style={{ background: "var(--accent-red)", color: "white" }}
                onClick={() => {
                  const probe = mobileConfirmProbe;
                  setMobileConfirmProbe(null);
                  if (onSetKey) onSetKey(probe);
                }}
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default CredentialsTab;
