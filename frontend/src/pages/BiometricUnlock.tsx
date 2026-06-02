import { useCallback, useEffect, useState } from "react";

type UnlockStatus = "idle" | "prompting" | "success" | "error";

/** A stored WebAuthn credential as returned by the backend listing endpoint. */
interface StoredCredential {
  credential_id: string;
  label: string | null;
  created_at: number;
}

/** Begin-registration options returned by the server. */
interface RegisterBeginOptions {
  challenge: string;
  rp: PublicKeyCredentialRpEntity;
  user: { id: string; name: string };
  timeout_ms?: number;
}

/** Begin-assertion options returned by the server. */
interface AssertBeginOptions {
  challenge: string;
  allow_credentials?: Array<{ id: string; type: PublicKeyCredentialType }>;
  timeout_ms?: number;
}

/**
 * Narrow the `PublicKeyCredential` constructor that exposes
 * `isUserVerifyingPlatformAuthenticatorAvailable`. The static method is part of
 * the WebAuthn spec but is declared on the constructor rather than instances.
 */
function getPlatformAuthenticatorChecker():
  | (() => Promise<boolean>)
  | null {
  if (typeof window === "undefined" || !("PublicKeyCredential" in window)) {
    return null;
  }
  const pkc = window.PublicKeyCredential;
  if (
    typeof pkc?.isUserVerifyingPlatformAuthenticatorAvailable === "function"
  ) {
    return () => pkc.isUserVerifyingPlatformAuthenticatorAvailable();
  }
  return null;
}

export function BiometricUnlock() {
  const [status, setStatus] = useState<UnlockStatus>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [isSupported, setIsSupported] = useState<boolean | null>(null);
  const [credentials, setCredentials] = useState<StoredCredential[]>([]);

  useEffect(() => {
    // Check if WebAuthn is supported in this browser
    const checkAvailable = getPlatformAuthenticatorChecker();
    setIsSupported(checkAvailable !== null);

    if (checkAvailable) {
      checkAvailable().then((available) => setIsSupported(available));
    }

    // Load existing credentials
    fetch("/api/auth/webauthn/credentials", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r): Promise<{ credentials?: StoredCredential[] }> => {
        if (!r.ok) return Promise.resolve({ credentials: [] });
        return r.json();
      })
      .then((data) => setCredentials(data.credentials ?? []))
      .catch(() => setCredentials([]));
  }, []);

  const registerCredential = useCallback(async () => {
    if (!isSupported) {
      setStatus("error");
      setMessage("Biometric authentication is not supported on this device.");
      return;
    }

    setStatus("prompting");
    setMessage(null);

    try {
      // Step 1: Begin registration
      const beginResp = await fetch("/api/auth/webauthn/register/begin", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ label: "Mobile biometric" }),
      });
      if (!beginResp.ok) {
        const err = await beginResp.json();
        throw new Error(err.detail || "Registration begin failed");
      }
      const options: RegisterBeginOptions = await beginResp.json();

      // Step 2: Call navigator.credentials.create with server options
      const created = await navigator.credentials.create({
        publicKey: {
          challenge: base64urlToBuffer(options.challenge),
          rp: options.rp,
          user: {
            id: new TextEncoder().encode(options.user.id),
            name: options.user.name,
            displayName: options.user.name,
          },
          pubKeyCredParams: [{ alg: -7, type: "public-key" }],
          authenticatorSelection: {
            authenticatorAttachment: "platform",
            userVerification: "required",
          },
          timeout: options.timeout_ms,
        },
      });

      if (!created) {
        throw new Error("Credential creation was cancelled");
      }
      const credential = created as PublicKeyCredential;
      const attestation =
        credential.response as AuthenticatorAttestationResponse;

      // Step 3: Complete registration (backend is stubbed — will 501 until verifier is pinned)
      const completeResp = await fetch("/api/auth/webauthn/register/complete", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          credential: {
            id: credential.id,
            rawId: bufferToBase64url(credential.rawId),
            type: credential.type,
            response: {
              clientDataJSON: bufferToBase64url(attestation.clientDataJSON),
              attestationObject: bufferToBase64url(
                attestation.attestationObject
              ),
            },
          },
        }),
      });

      if (completeResp.status === 501) {
        setStatus("success");
        setMessage(
          "Biometric registration captured on device. Backend verification is not yet implemented (501)."
        );
        // Refresh credentials list optimistically
        setCredentials((prev) => [
          ...prev,
          {
            credential_id: credential.id,
            label: "Mobile biometric",
            created_at: Date.now() / 1000,
          },
        ]);
        return;
      }

      if (!completeResp.ok) {
        const err = await completeResp.json();
        throw new Error(err.detail || "Registration complete failed");
      }

      setStatus("success");
      setMessage("Biometric credential registered successfully.");
    } catch (e) {
      setStatus("error");
      setMessage((e instanceof Error ? e.message : String(e)) || "Registration failed");
    }
  }, [isSupported]);

  const authenticate = useCallback(async () => {
    if (!isSupported) {
      setStatus("error");
      setMessage("Biometric authentication is not supported on this device.");
      return;
    }

    setStatus("prompting");
    setMessage(null);

    try {
      // Step 1: Begin assertion
      const beginResp = await fetch("/api/auth/webauthn/assert/begin", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({}),
      });
      if (!beginResp.ok) {
        const err = await beginResp.json();
        throw new Error(err.detail || "Assertion begin failed");
      }
      const options: AssertBeginOptions = await beginResp.json();

      // Step 2: Call navigator.credentials.get
      const asserted = await navigator.credentials.get({
        publicKey: {
          challenge: base64urlToBuffer(options.challenge),
          allowCredentials: (options.allow_credentials || []).map((c) => ({
            id: base64urlToBuffer(c.id),
            type: c.type,
          })),
          userVerification: "required",
          timeout: options.timeout_ms,
        },
      });

      if (!asserted) {
        throw new Error("Assertion was cancelled");
      }
      const assertion = asserted as PublicKeyCredential;
      const assertionResponse =
        assertion.response as AuthenticatorAssertionResponse;

      // Step 3: Complete assertion (backend is stubbed — will 501 until verifier is pinned)
      const completeResp = await fetch("/api/auth/webauthn/assert/complete", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({
          credential: {
            id: assertion.id,
            rawId: bufferToBase64url(assertion.rawId),
            type: assertion.type,
            response: {
              authenticatorData: bufferToBase64url(
                assertionResponse.authenticatorData
              ),
              clientDataJSON: bufferToBase64url(
                assertionResponse.clientDataJSON
              ),
              signature: bufferToBase64url(assertionResponse.signature),
              userHandle: assertionResponse.userHandle
                ? bufferToBase64url(assertionResponse.userHandle)
                : null,
            },
          },
        }),
      });

      if (completeResp.status === 501) {
        setStatus("success");
        setMessage(
          "Biometric authentication captured on device. Backend verification is not yet implemented (501)."
        );
        return;
      }

      if (!completeResp.ok) {
        const err = await completeResp.json();
        throw new Error(err.detail || "Assertion complete failed");
      }

      setStatus("success");
      setMessage("Biometric authentication successful.");
    } catch (e) {
      setStatus("error");
      setMessage((e instanceof Error ? e.message : String(e)) || "Authentication failed");
    }
  }, [isSupported]);

  const revokeCredential = useCallback(
    async (credentialId: string) => {
      try {
        const resp = await fetch(`/api/auth/webauthn/credentials/${credentialId}`, {
          method: "DELETE",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (!resp.ok) {
          const err = await resp.json();
          throw new Error(err.detail || "Revoke failed");
        }
        setCredentials((prev) => prev.filter((c) => c.credential_id !== credentialId));
        setMessage("Credential revoked.");
      } catch (e) {
        setStatus("error");
        setMessage((e instanceof Error ? e.message : String(e)) || "Revoke failed");
      }
    },
    []
  );

  return (
    <div className="glass-card" style={{ padding: "16px", margin: "16px" }}>
      <h2 style={{ fontSize: "16px", marginBottom: "12px" }}>
        Mobile Biometric Unlock
      </h2>

      {isSupported === false && (
        <div
          style={{
            color: "var(--accent-yellow)",
            fontSize: "12px",
            marginBottom: "8px",
          }}
        >
          Your browser or device does not support biometric authentication.
        </div>
      )}

      {message && (
        <div
          style={{
            color:
              status === "error"
                ? "var(--accent-red)"
                : "var(--accent-green)",
            fontSize: "12px",
            marginBottom: "8px",
          }}
        >
          {message}
        </div>
      )}

      {status === "prompting" && (
        <div style={{ fontSize: "12px", marginBottom: "8px", color: "var(--text-secondary)" }}>
          Follow your device prompt to authenticate...
        </div>
      )}

      <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
        <button
          className="touch-button touch-button-primary"
          disabled={!isSupported || status === "prompting"}
          onClick={authenticate}
          type="button"
        >
          Unlock with Biometrics
        </button>
        <button
          className="touch-button touch-button-secondary"
          disabled={!isSupported || status === "prompting"}
          onClick={registerCredential}
          type="button"
        >
          Register Device
        </button>
      </div>

      {credentials.length > 0 && (
        <div>
          <h3 style={{ fontSize: "14px", marginBottom: "8px" }}>
            Registered Credentials
          </h3>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {credentials.map((cred) => (
              <li
                key={cred.credential_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "8px 0",
                  borderBottom: "1px solid var(--border)",
                  fontSize: "13px",
                }}
              >
                <span>
                  {cred.label || "Unnamed credential"}
                  <span
                    style={{
                      color: "var(--text-secondary)",
                      fontSize: "11px",
                      marginLeft: "8px",
                    }}
                  >
                    {new Date(cred.created_at * 1000).toLocaleDateString()}
                  </span>
                </span>
                <button
                  className="touch-button touch-button-danger"
                  style={{ padding: "4px 8px", fontSize: "12px" }}
                  onClick={() => revokeCredential(cred.credential_id)}
                  type="button"
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

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

function bufferToBase64url(buffer: ArrayBuffer): string {
  const binary = String.fromCharCode(...new Uint8Array(buffer));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
