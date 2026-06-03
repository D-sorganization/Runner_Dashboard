import { useCallback, useEffect, useState } from "react";
import { Badge } from "../primitives/Badge";
import { EmptyState } from "../primitives/EmptyState";
import { SkeletonCard, SkeletonLine } from "../primitives/Skeleton";
import { TouchButton } from "../primitives/TouchButton";

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
      .catch((e: unknown) => {
        setError((e instanceof Error ? e.message : String(e)) || "Failed to load workspaces");
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
        className="glass-card linear-setup linear-setup--loading"
      >
        <SkeletonLine height={18} width="55%" />
        <SkeletonCard lines={2} />
        <SkeletonCard lines={3} />
      </div>
    );
  }

  return (
    <div className="glass-card linear-setup">
      <h2 className="linear-setup__title">Linear Integration Setup</h2>

      {error && (
        <EmptyState
          variant="error"
          title="Failed to load Linear workspaces"
          description={error}
        />
      )}

      {saveMsg && (
        <div className="linear-setup__success" role="status">
          {saveMsg}
        </div>
      )}

      {/* Webhook URL section */}
      <section className="linear-setup__panel">
        <div className="linear-setup__section-title">
          Dashboard Webhook URL
        </div>
        <div className="linear-setup__webhook-row">
          <code className="linear-setup__webhook-code">
            {webhookUrl}
          </code>
          <TouchButton
            onClick={copyWebhookUrl}
            className="linear-setup__copy-button"
            variant="primary"
          >
            Copy
          </TouchButton>
        </div>
        <p className="linear-setup__hint">
          Paste this URL into your Linear workspace webhook settings.
        </p>
      </section>

      {/* Workspaces list */}
      <section className="linear-setup__section">
        <div className="linear-setup__section-title">
          Configured Workspaces ({workspaces.length})
        </div>

        {workspaces.length === 0 && (
          <EmptyState
            title="No workspaces configured"
            description="Add a workspace in config/linear.json."
          />
        )}

        {workspaces.map((ws) => (
          <article key={ws.id} className="linear-setup__workspace">
            <div className="linear-setup__workspace-header">
              <span className="linear-setup__workspace-id">{ws.id}</span>
              <Badge
                tone={ws.auth_status === "ok" || ws.auth_status === "active" ? "success" : "danger"}
                size="sm"
              >
                {ws.auth_status}
              </Badge>
            </div>

            <WorkspaceField label="Auth" value={ws.auth_kind} />
            <WorkspaceField label="Teams" value={ws.teams_filter.join(", ")} />
            <WorkspaceField label="Trigger label" value={ws.trigger_label || "—"} />
            <WorkspaceField label="Default repository" value={ws.default_repository || "—"} />
            <WorkspaceField label="Prefer source" value={ws.prefer_source} />
          </article>
        ))}
      </section>

      {/* Setup instructions */}
      <section className="linear-setup__panel linear-setup__instructions">
        <div className="linear-setup__section-title">
          Setup Steps
        </div>
        <ol className="linear-setup__steps">
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
      </section>
    </div>
  );
}

function WorkspaceField({ label, value }: { label: string; value: string }) {
  return (
    <div className="linear-setup__field">
      {label}: <strong>{value}</strong>
    </div>
  );
}
