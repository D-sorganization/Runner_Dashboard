import type React from "react";
import type { CodeRequestRecord } from "./codeRequestsTypes";
import { requestDate, requestStatus, requestVoteCount } from "./codeRequestsTypes";

export interface CodeRequestsHistoryProps {
  requests: CodeRequestRecord[];
  loading?: boolean;
}

export function CodeRequestsHistory({
  requests,
  loading,
}: CodeRequestsHistoryProps): React.ReactElement {
  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Dispatch History</div>
      {loading ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>Loading…</div>
      ) : requests.length === 0 ? (
        <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
          No dispatched requests yet.
        </div>
      ) : (
        <div
          className="feature-request-desktop-history"
          style={{ maxHeight: 300, overflowY: "auto" }}
        >
          {requests.slice(0, 50).map((r, i) => (
            <div
              key={i}
              style={{
                borderBottom: "1px solid var(--border)",
                padding: "8px 0",
                fontSize: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                  {r.repository}
                </span>
                <span style={{ color: "var(--text-muted)" }}>{requestDate(r)}</span>
              </div>
              <div style={{ color: "var(--text-secondary)", marginTop: 2 }}>
                {(r.prompt || "").slice(0, 100) +
                  ((r.prompt || "").length > 100 ? "…" : "")}
              </div>
              <div style={{ marginTop: 4, display: "flex", gap: 8 }}>
                <span
                  style={{
                    color:
                      requestStatus(r) === "failed"
                        ? "var(--accent-red)"
                        : "var(--text-muted)",
                  }}
                >
                  {requestStatus(r)}
                </span>
                <span style={{ color: "var(--text-muted)" }}>{r.provider || ""}</span>
                {(r.standards || []).map((s) => (
                  <span key={s} style={{ color: "var(--accent-purple)", fontSize: 11 }}>
                    {s.toUpperCase()}
                  </span>
                ))}
              </div>
              {r.error ? (
                <div style={{ color: "var(--accent-red)", marginTop: 2 }}>{r.error}</div>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {requests.length > 0 ? (
        <div
          className="feature-request-mobile-list"
          aria-label="Feature request history"
        >
          {requests.slice(0, 50).map((r, i) => (
            <article key={"mobile-feature-" + i} className="feature-request-mobile-card">
              <div className="feature-request-mobile-title">
                <span>{r.repository || "Unknown repository"}</span>
                <span className="feature-request-mobile-chip feature-request-mobile-status">
                  {requestStatus(r)}
                </span>
              </div>
              <div className="feature-request-mobile-prompt">
                {(r.prompt || "").slice(0, 180) +
                  ((r.prompt || "").length > 180 ? "…" : "")}
              </div>
              {r.error ? (
                <div className="feature-request-mobile-prompt" style={{ color: "var(--accent-red)" }}>
                  {r.error}
                </div>
              ) : null}
              <div className="feature-request-mobile-meta">
                <span className="feature-request-mobile-chip">
                  {requestVoteCount(r) + " votes"}
                </span>
                <span className="feature-request-mobile-chip">
                  {r.provider || "provider unknown"}
                </span>
                <span className="feature-request-mobile-chip">
                  {requestDate(r) || "date unknown"}
                </span>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </div>
  );
}
