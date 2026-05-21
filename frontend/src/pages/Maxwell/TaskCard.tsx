import type { MaxwellTask } from "./mobileTypes";
import { elapsedLabel } from "./mobileTypes";

interface TaskCardProps {
  task: MaxwellTask;
}

export function TaskCard({ task }: TaskCardProps) {
  const s = task.status?.toLowerCase() ?? "unknown";
  const accent =
    s === "running" || s === "active"
      ? "var(--accent-green)"
      : s === "error" || s === "failed"
        ? "var(--accent-red)"
        : "var(--text-muted)";

  return (
    <div
      aria-label={`Task ${task.task_id}, status ${task.status}`}
      className="maxwell-task-card"
      role="listitem"
      style={{
        background: "var(--bg-secondary)",
        border: "1px solid var(--border)",
        borderLeft: `3px solid ${accent}`,
        borderRadius: 10,
        flexShrink: 0,
        minWidth: 140,
        padding: "10px 12px",
      }}
    >
      <div
        style={{
          color: "var(--text-primary)",
          fontSize: 12,
          fontWeight: 600,
          marginBottom: 4,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          maxWidth: 120,
        }}
        title={task.task_id}
      >
        {task.task_id}
      </div>
      <div style={{ color: accent, fontSize: 11, marginBottom: 2 }}>
        {task.status}
      </div>
      <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
        {elapsedLabel(task)}
      </div>
    </div>
  );
}
