import { Badge } from "../../primitives/Badge";
import type { FilterValue, WorkflowRun } from "./mobileTypes";
import { statusLabel, statusTone } from "./mobileTypes";

interface RunCardProps {
  elapsed: string;
  repo: string;
  run: WorkflowRun;
  status: FilterValue;
  onClick: () => void;
}

export function MobileRunCard({
  elapsed,
  repo,
  run,
  status,
  onClick,
}: RunCardProps) {
  return (
    <button
      aria-label={`${run.name ?? "Workflow run"} in ${repo}, ${statusLabel(status)}, ${elapsed}`}
      className="queue-mobile-run-card"
      onClick={onClick}
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        cursor: "pointer",
        display: "block",
        marginBottom: 10,
        padding: "12px 14px",
        textAlign: "left",
        width: "100%",
      }}
      type="button"
    >
      <div
        style={{
          alignItems: "center",
          display: "flex",
          justifyContent: "space-between",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            color: "var(--text-primary)",
            fontSize: 13,
            fontWeight: 600,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            maxWidth: "60%",
          }}
        >
          {run.name ?? "Workflow run"}
        </span>
        <Badge tone={statusTone(status)} size="sm">
          {statusLabel(status)}
        </Badge>
      </div>
      <div
        style={{
          color: "var(--text-secondary)",
          display: "flex",
          fontSize: 12,
          gap: 8,
          flexWrap: "wrap",
        }}
      >
        <span>{repo || "unknown repo"}</span>
        {run.head_branch && (
          <span style={{ color: "var(--text-muted)" }}>{run.head_branch}</span>
        )}
        <span style={{ color: "var(--text-muted)", marginLeft: "auto" }}>
          {elapsed}
        </span>
      </div>
    </button>
  );
}
