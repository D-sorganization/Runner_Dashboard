import { useCallback, useEffect, useState } from "react";
import { SkeletonCard, SkeletonLine } from "../primitives/Skeleton";

interface WorkspaceSummary {
  id: string;
  auth_kind: string;
  auth_status: string;
  teams_filter: string[];
  trigger_label: string;
  default_repository: string;
  prefer_source: string;
}

export function LinearSetup() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [webhookUrl, setWebhookUrl] = useState<string>("");
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/linear/workspaces")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data: { workspaces: WorkspaceSummary[] }) => {
        setWorkspaces(data.workspaces || []);
        setLoading(false);
      })
      .catch((e: any) => {
        setError(e.message || "Failed to load workspaces");
        setLoading(false);
      });

    // Derive webhook URL from current location
    const base = window.location.origin;
    setWebhookUrl(`${base}/api/linear/webhook`);
  }, []);

  const copyWebhookUrl = useCallback(() => {
    navigator.clipboard.writeText(webhookUrl).catch(() => {});
    setSaveMsg("Webhook URL copied to clipboard");
    setTimeout(() => setSaveMsg(null), 3000);
  }, [webhookUrl]);

  if (loading) {
    return (
      <div
        aria-busy="true"
        aria-label="Loading Linear workspace configuration"
        className="glass-card"
        style={{
          padding: "16px",
          margin: "16px",
          display: "flex",
          flexDirection: "column",
          gap: "12px",
        }}
      >
        <SkeletonLine height={18} width="55%" />
        <SkeletonCard lines={2} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  return (
    <div className="glass-card" style={{ padding: "16px", margin: "16px" }}>
      <h2 style={{ fontSize: "16px", marginBottom: "12px" }}>Linear Integration Setup</h2>

      {error && (
        <div
          style={{
            color: "var(--accent-red)",
            fontSize: "12px",
            marginBottom: "8px",
            padding: "8px",
            background: "rgba(239, 68, 68, 0.08)",
            borderRadius: "4px",
          }}
        >
          {error}
        </div>
      )}

      {saveMsg && (
        <div
          style={{
            color: "var(--accent-green)",
            fontSize: "12px",
            marginBottom: "8px",
            padding: "8px",
            background: "rgba(34, 197, 94, 0.08)",
            borderRadius: "4px",
          }}
        >
          {saveMsg}
        </div>
      )}

      {/* Webhook URL section */}
      <div
        style={{
          marginBottom: "16px",
          padding: "12px",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          background: "var(--bg-secondary)",
        }}
      >
        <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "6px" }}>
          Dashboard Webhook URL
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <code
            style={{
              flex: 1,
              fontSize: "12px",
              background: "var(--bg-primary)",
              padding: "6px 8px",
              borderRadius: "4px",
              wordBreak: "break-all",
            }}
          >
            {webhookUrl}
          </code>
          <button
            onClick={copyWebhookUrl}
            className="btn btn-sm btn-blue"
            style={{ whiteSpace: "nowrap", fontSize: "12px", padding: "4px 8px" }}
          >
            Copy
          </button>
        </div>
        <p style={{ fontSize: "11px", color: "var(--text-secondary)", marginTop: "6px" }}>
          Paste this URL into your Linear workspace webhook settings.
        </p>
      </div>

      {/* Workspaces list */}
      <div style={{ marginBottom: "12px" }}>
        <div style={{ fontSize: "13px", fontWeight: 600, marginBottom: "8px" }}>
          Configured Workspaces ({workspaces.length})
        </div>

        {workspaces.length === 0 && (
          <p style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
            No workspaces configured. Add a workspace in <code>config/linear.json</code>.
          </p>
        )}

        {workspaces.map((ws) => (
          <div
            key={ws.id}
            style={{
              marginBottom: "12px",
              padding: "10px",
              border: "1px solid var(--border)",
              borderRadius: "6px",
              background: "var(--bg-secondary)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "6px",
              }}
            >
              <span style={{ fontSize: "13px", fontWeight: 600 }}>{ws.id}</span>
              <span
                style={{
                  fontSize: "11px",
                  padding: "2px 6px",
                  borderRadius: "4px",
                  background:
                    ws.auth_status === "ok"
                      ? "rgba(34, 197, 94, 0.12)"
                      : "rgba(239, 68, 68, 0.12)",
                  color:
                    ws.auth_status === "ok" ? "var(--accent-green)" : "var(--accent-red)",
                }}
              >
                {ws.auth_status}
              </span>
            </div>

            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "4px" }}>
              Auth: <strong style={{ color: "var(--text-primary)" }}>{ws.auth_kind}</strong>
            </div>

            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "4px" }}>
              Teams: <strong style={{ color: "var(--text-primary)" }}>{ws.teams_filter.join(", ")}</strong>
            </div>

            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "4px" }}>
              Trigger label: <strong style={{ color: "var(--text-primary)" }}>{ws.trigger_label || "—"}</strong>
            </div>

            <div style={{ fontSize: "12px", color: "var(--text-secondary)", marginBottom: "4px" }}>
              Default repository: <strong style={{ color: "var(--text-primary)" }}>{ws.default_repository || "—"}</strong>
            </div>

            <div style={{ fontSize: "12px", color: "var(--text-secondary)" }}>
              Prefer source: <strong style={{ color: "var(--text-primary)" }}>{ws.prefer_source}</strong>
            </div>
          </div>
        ))}
      </div>

      {/* Setup instructions */}
      <div
        style={{
          marginTop: "16px",
          padding: "12px",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          background: "var(--bg-secondary)",
          fontSize: "12px",
          color: "var(--text-secondary)",
          lineHeight: 1.6,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: "6px", color: "var(--text-primary)" }}>
          Setup Steps
        </div>
        <ol style={{ paddingLeft: "16px", margin: 0 }}>
          <li>
            Create <code>config/linear.json</code> with workspace definitions.
          </li>
          <li>
            Set <code>LINEAR_API_KEY</code> in your environment or <code>~/.config/runner-dashboard/env</code>.
          </li>
          <li>
            Set <code>LINEAR_WEBHOOK_SECRET</code> for webhook signature verification.
          </li>
          <li>
            Paste the webhook URL above into your Linear workspace settings.
          </li>
        </ol>
      </div>
    </div>
  );
}
