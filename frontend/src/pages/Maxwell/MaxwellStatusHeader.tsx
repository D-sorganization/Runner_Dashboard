import type { CSSProperties } from "react";
import { TouchButton } from "../../primitives/TouchButton";
import { statusEmoji, statusPillStyle } from "./mobileTypes";

interface MaxwellStatusHeaderProps {
  daemonStatus: string;
  daemonVersion: string;
  statusError: string | null;
  statusLoading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onOpenControls: () => void;
}

export function MaxwellStatusHeader({
  daemonStatus,
  daemonVersion,
  statusError,
  statusLoading,
  refreshing,
  onRefresh,
  onOpenControls,
}: MaxwellStatusHeaderProps) {
  const pillStyle: CSSProperties = {
    ...statusPillStyle(daemonStatus),
    borderRadius: 20,
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    fontSize: 12,
    fontWeight: 600,
    padding: "4px 10px",
  };

  return (
    <div
      style={{
        alignItems: "center",
        display: "flex",
        gap: 8,
        justifyContent: "space-between",
        marginBottom: 14,
        flexWrap: "wrap",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <span
          aria-label={`Maxwell daemon status: ${daemonStatus}`}
          style={pillStyle}
        >
          <span aria-hidden="true">{statusEmoji(daemonStatus)}</span>
          {daemonStatus}
        </span>
        {daemonVersion && (
          <span style={{ color: "var(--text-muted)", fontSize: 11 }}>
            v{daemonVersion}
          </span>
        )}
        {statusError && (
          <span
            aria-live="assertive"
            role="alert"
            style={{ color: "var(--accent-red)", fontSize: 11 }}
          >
            {statusError}
          </span>
        )}
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        <TouchButton
          aria-label="Refresh Maxwell status"
          disabled={statusLoading || refreshing}
          onClick={onRefresh}
          variant="default"
          style={{ fontSize: 12, minHeight: 34, padding: "4px 10px" }}
        >
          {refreshing ? "Refreshing…" : "↻ Refresh"}
        </TouchButton>
        <TouchButton
          aria-label="Open daemon controls"
          data-testid="maxwell-settings-btn"
          onClick={onOpenControls}
          variant="default"
          style={{ fontSize: 12, minHeight: 34, padding: "4px 10px" }}
        >
          ⚙ Controls
        </TouchButton>
      </div>
    </div>
  );
}
