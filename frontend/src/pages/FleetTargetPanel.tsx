import React, { useCallback, useState } from "react";
import { legacyFetch } from "../lib/api";
import { TouchButton } from "../primitives/TouchButton";

export interface FleetTargetPanelProps {
  host: string;
  cap: number;
  desired?: number;
  overrideRunners?: number | null;
  overrideUntil?: string | null;
  onChanged?: () => void;
}

export function FleetTargetPanel({
  host,
  cap,
  desired,
  overrideRunners,
  overrideUntil,
  onChanged,
}: FleetTargetPanelProps): React.ReactElement {
  const [runners, setRunners] = useState(overrideRunners ?? desired ?? 0);
  const [until, setUntil] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(() => {
    setBusy(true);
    setError(null);
    legacyFetch("/api/fleet/target", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({
        host,
        runners: Math.min(cap, Math.max(0, Number(runners))),
        until: until.trim() || undefined,
        reason: reason.trim() || "Fleet UI slider",
        apply: true,
      }),
    })
      .then((r) => {
        if (!r.ok) return r.text().then((t) => Promise.reject(new Error(t || `HTTP ${r.status}`)));
        return r.json();
      })
      .then(() => {
        onChanged?.();
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "target update failed");
      })
      .finally(() => setBusy(false));
  }, [host, cap, runners, until, reason, onChanged]);

  return (
    <div className="card fleet-target-panel" style={{ marginTop: 12 }}>
      <div className="section-title">Target until expiry</div>
      <p className="stat-sub" style={{ marginBottom: 8 }}>
        Cap {cap}
        {overrideUntil ? ` · override until ${overrideUntil}` : ""}
      </p>
      <label className="metric-row" style={{ alignItems: "center", gap: 12 }}>
        <span className="metric-label">Runners</span>
        <input
          type="range"
          min={0}
          max={cap}
          value={Math.min(cap, Math.max(0, Number(runners)))}
          onChange={(e) => setRunners(Number(e.target.value))}
          aria-label={`Runner target for ${host}`}
        />
        <span className="metric-value">{runners}</span>
      </label>
      <label className="metric-row" style={{ marginTop: 8 }}>
        <span className="metric-label">Until</span>
        <input
          type="text"
          placeholder="18:00 or ISO time"
          value={until}
          onChange={(e) => setUntil(e.target.value)}
          className="input"
          style={{ flex: 1 }}
        />
      </label>
      <label className="metric-row" style={{ marginTop: 8 }}>
        <span className="metric-label">Reason</span>
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="input"
          style={{ flex: 1 }}
        />
      </label>
      {error ? (
        <p role="alert" style={{ color: "var(--accent-red)", marginTop: 8 }}>
          {error}
        </p>
      ) : null}
      <TouchButton
        variant="primary"
        style={{ marginTop: 12 }}
        disabled={busy}
        onClick={apply}
      >
        {busy ? "Applying…" : "Set target"}
      </TouchButton>
    </div>
  );
}
