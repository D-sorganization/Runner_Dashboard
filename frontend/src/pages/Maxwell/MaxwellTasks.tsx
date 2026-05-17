import type { CSSProperties } from "react";
import { TaskCard } from "./TaskCard";
import type { MaxwellTask } from "./mobileTypes";

interface MaxwellTasksProps {
  tasksLoading: boolean;
  tasks: MaxwellTask[];
  isRunning: boolean;
}

export function MaxwellTasks({
  tasksLoading,
  tasks,
  isRunning,
}: MaxwellTasksProps) {
  if (tasksLoading) {
    return (
      <div
        style={{
          display: "flex",
          gap: 8,
          overflowX: "auto",
          paddingBottom: 4,
        }}
      >
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              flexShrink: 0,
              height: 72,
              minWidth: 140,
            }}
          />
        ))}
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <div
        aria-label="No active tasks"
        style={{
          color: "var(--text-muted)",
          fontSize: 12,
          padding: "8px 0",
        }}
      >
        {isRunning ? "No active tasks." : "Daemon not running — no task history."}
      </div>
    );
  }

  return (
    <div
      aria-label="Active tasks"
      role="list"
      style={{
        display: "flex",
        gap: 8,
        overflowX: "auto",
        paddingBottom: 6,
        WebkitOverflowScrolling:
          "touch" as CSSProperties["WebkitOverflowScrolling"],
      }}
    >
      {tasks.map((task) => (
        <TaskCard key={task.task_id} task={task} />
      ))}
    </div>
  );
}
