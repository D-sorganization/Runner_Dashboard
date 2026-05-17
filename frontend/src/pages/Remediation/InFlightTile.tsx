import { useEffect, useRef, useState } from "react";
import { Badge } from "../../primitives/Badge";
import type { InFlightDispatch } from "./mobileTypes";
import { elapsedLabel } from "./mobileTypes";

interface InFlightTileProps {
  dispatch: InFlightDispatch;
}

export function InFlightTile({ dispatch }: InFlightTileProps) {
  const [, forceUpdate] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    intervalRef.current = setInterval(() => forceUpdate((n) => n + 1), 5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, []);

  const tone =
    dispatch.status === "done"
      ? "success"
      : dispatch.status === "error"
        ? "danger"
        : "info";

  return (
    <div
      aria-label={`In-flight dispatch: ${dispatch.itemTitle}`}
      className="remediation-inflight-tile"
      role="status"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderLeft: "4px solid var(--accent-blue)",
        borderRadius: 10,
        marginBottom: 10,
        padding: "12px 14px",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            color: "var(--text-primary)",
            fontWeight: 600,
            fontSize: 13,
          }}
        >
          {dispatch.itemTitle}
        </span>
        <Badge tone={tone}>{dispatch.status}</Badge>
      </div>
      <div
        style={{
          color: "var(--text-secondary)",
          fontSize: 12,
          display: "flex",
          gap: 10,
          flexWrap: "wrap",
        }}
      >
        <span>Agent: {dispatch.providerLabel}</span>
        <span>Repo: {dispatch.repository}</span>
        <span>Elapsed: {elapsedLabel(dispatch.startedAt)}</span>
        <span>Heartbeat: {elapsedLabel(dispatch.lastHeartbeat)} ago</span>
      </div>
    </div>
  );
}
