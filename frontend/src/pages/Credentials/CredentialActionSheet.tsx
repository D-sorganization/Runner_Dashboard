import { useCallback, useEffect, useState } from "react";
import { BottomSheet } from "../../primitives/BottomSheet";
import type { CredentialProbe } from "./mobileTypes";
import { PROVIDER_MAP } from "./mobileTypes";

interface CredentialActionSheetProps {
  probe: CredentialProbe | null;
  onClose: () => void;
  onKeySet: () => void;
}

export function CredentialActionSheet({
  probe,
  onClose,
  onKeySet,
}: CredentialActionSheetProps) {
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
      setSubmitError(
        (e instanceof Error ? e.message : String(e)) || "Failed to set key",
      );
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
      setSubmitError(
        (e instanceof Error ? e.message : String(e)) || "Failed to clear key",
      );
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
            <div
              style={{
                color: "var(--text-secondary)",
                fontSize: "13px",
                marginBottom: "4px",
              }}
            >
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
            <div style={{ color: "var(--accent-red)", fontSize: "13px" }}>
              {submitError}
            </div>
          )}
          {submitSuccess && (
            <div
              style={{
                color: "var(--accent-green, #22c55e)",
                fontSize: "13px",
              }}
            >
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
                  style={{
                    borderRadius: "8px",
                    fontSize: "14px",
                    minHeight: "44px",
                  }}
                  type="button"
                >
                  Set API Key
                </button>
                {probe.authenticated && (
                  <button
                    className="touch-button touch-button-danger"
                    disabled={submitting}
                    onClick={handleClearKey}
                    style={{
                      borderRadius: "8px",
                      fontSize: "14px",
                      minHeight: "44px",
                    }}
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
          <p
            style={{
              color: "var(--text-secondary)",
              fontSize: "13px",
              margin: 0,
            }}
          >
            Enter the API key for <strong>{probe.label}</strong>. It will be
            written to the server-side env files and never returned to the
            browser.
          </p>

          <label style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span
              style={{
                color: "var(--text-secondary)",
                fontSize: "12px",
                fontWeight: 600,
              }}
            >
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
            <div style={{ color: "var(--accent-red)", fontSize: "13px" }}>
              {submitError}
            </div>
          )}
          {submitSuccess && (
            <div
              style={{
                color: "var(--accent-green, #22c55e)",
                fontSize: "13px",
              }}
            >
              Key set successfully.
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <button
              className="touch-button touch-button-primary"
              disabled={submitting || !keyValue.trim()}
              onClick={handleSetKey}
              style={{
                borderRadius: "8px",
                fontSize: "14px",
                minHeight: "44px",
              }}
              type="button"
            >
              {submitting ? "Saving…" : "Save Key"}
            </button>
            <button
              className="touch-button touch-button-secondary"
              disabled={submitting}
              onClick={() => setMode("actions")}
              style={{
                borderRadius: "8px",
                fontSize: "14px",
                minHeight: "44px",
              }}
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
