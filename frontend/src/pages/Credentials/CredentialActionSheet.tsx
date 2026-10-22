import { BottomSheet } from "../../primitives/BottomSheet";
import type { CredentialProbe } from "./mobileTypes";

interface CredentialActionSheetProps {
  probe: CredentialProbe | null;
  onClose: () => void;
  onKeySet: () => void;
}

/** Mobile bottom sheet: read-only probe details (subscription logins only; no API-key writes). */
export function CredentialActionSheet({ probe, onClose }: CredentialActionSheetProps) {
  if (!probe) return null;

  return (
    <BottomSheet isOpen={probe !== null} onClose={onClose} title={probe.label}>
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
    </BottomSheet>
  );
}
