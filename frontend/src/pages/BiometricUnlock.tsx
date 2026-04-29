import { useCallback, useEffect, useState } from "react";

type UnlockStatus = "idle" | "prompting" | "success" | "error";

export function BiometricUnlock() {
  const [status, setStatus] = useState<UnlockStatus>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [isSupported, setIsSupported] = useState<boolean | null>(null);
  const [credentials, setCredentials] = useState<
    Array<{ credential_id: string; label: string | null; created_at: number }>
  >([]);

  useEffect(() => {
    // Check if WebAuthn is supported in this browser
    const supported =
      typeof window !== "undefined" &&
      "PublicKeyCredential" in window &&
      typeof (window as any).PublicKeyCredential?.isUserVerifyingPlatformAuthenticatorAvailable === "function";
    setIsSupported(supported);

    if (supported) {
      (window as any).PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(
        (available: boolean) => setIsSupported(available)
      );
    }

    // Load existing credentials
    fetch("/api/auth/webauthn/credentials", {
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((r) => {
        if (!r.ok) return { credentials: [] };
        return r.json();
      })
      .then((data) => setCredentials(data.credentials || []))
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
      const options = await beginResp.json();

      // Step 2: Call navigator.credentials.create with server options
      const credential = await (navigator as any).credentials.create({
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

      if (!credential) {
        throw new Error("Credential creation was cancelled");
      }

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
              clientDataJSON: bufferToBase64url(
                (credential.response as any).clientDataJSON
              ),
              attestationObject: bufferToBase64url(
                (credential.response as any).attestationObject
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
    } catch (e: any) {
      setStatus("error");
      setMessage(e.message || "Registration failed");
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
      const options = await beginResp.json();

      // Step 2: Call navigator.credentials.get
      const assertion = await (navigator as any).credentials.get({
        publicKey: {
          challenge: base64urlToBuffer(options.challenge),
          allowCredentials: (options.allow_credentials || []).map((c: any) => ({
            id: base64urlToBuffer(c.id),
            type: c.type,
          })),
          userVerification: "required",
          timeout: options.timeout_ms,
        },
      });

      if (!assertion) {
        throw new Error("Assertion was cancelled");
      }

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
                (assertion.response as any).authenticatorData
              ),
              clientDataJSON: bufferToBase64url(
                (assertion.response as any).clientDataJSON
              ),
              signature: bufferToBase64url(
                (assertion.response as any).signature
              ),
              userHandle: (assertion.response as any).userHandle
                ? bufferToBase64url((assertion.response as any).userHandle)
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
    } catch (e: any) {
      setStatus("error");
      setMessage(e.message || "Authentication failed");
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
      } catch (e: any) {
        setStatus("error");
        setMessage(e.message || "Revoke failed");
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
