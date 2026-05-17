import type { LockState } from "./mobileTypes";

interface LockScreenProps {
  lockState: LockState;
  lockError: string | null;
  onUnlock: () => void;
}

export function LockScreen({ lockState, lockError, onUnlock }: LockScreenProps) {
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
        <h2
          style={{
            color: "var(--text-primary)",
            fontSize: "18px",
            fontWeight: 700,
            margin: 0,
          }}
        >
          Credentials Locked
        </h2>
        <p
          style={{
            color: "var(--text-secondary)",
            fontSize: "13px",
            margin: "8px 0 0",
          }}
        >
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

      <p
        style={{
          color: "var(--text-muted, #6b7280)",
          fontSize: "11px",
          margin: 0,
        }}
      >
        Auto-locks after 60 seconds of inactivity.
      </p>
    </div>
  );
}
