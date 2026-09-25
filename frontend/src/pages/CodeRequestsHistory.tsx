import type React from "react";
import { tabIdToPath } from "../shell/routing";
import type { CodeRequestRecord } from "./codeRequestsTypes";
import { requestDate, requestStatus, requestVoteCount } from "./codeRequestsTypes";

function proposeToBoardHref(r: CodeRequestRecord): string {
  const params: Record<string, string> = {
    section: "proposals",
    title: (r.prompt || "").slice(0, 80).trim(),
    repo: r.repository || "",
    problem: r.prompt || "",
  };
  if (r.id) {
    // Deep link back to the originating Code Request so the Board can trace
    // the proposal to its source (#1284 review defect 10).
    params.code_request_url = `${window.location.origin}${tabIdToPath("code-requests")}?id=${encodeURIComponent(r.id)}`;
  }
  const p = new URLSearchParams(params);
  return `${tabIdToPath("fleet-command")}?${p.toString()}`;
}

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
                <a
                  href={proposeToBoardHref(r)}
                  className="feature-request-propose-link"
                  style={{
                    marginLeft: "auto",
                    fontSize: 11,
                    color: "var(--accent-blue, #3b82f6)",
                    textDecoration: "none",
                    fontWeight: 500,
                  }}
                >
                  Propose to Board →
                </a>
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
                <a
                  href={proposeToBoardHref(r)}
                  className="feature-request-mobile-chip"
                  style={{ color: "var(--accent-blue, #3b82f6)", textDecoration: "none" }}
                >
                  Propose to Board →
                </a>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </div>
  );
}
