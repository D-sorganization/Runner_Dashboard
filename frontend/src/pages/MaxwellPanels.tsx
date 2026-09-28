import React from "react";
import type { MaxwellStatus, MaxwellTask } from "./MaxwellPage";

interface MaxwellTasksPanelProps {
  status: MaxwellStatus;
  tasks: MaxwellTask[];
  tasksLoading: boolean;
}

export function MaxwellTasksPanel({
  status,
  tasks,
  tasksLoading,
}: MaxwellTasksPanelProps): React.ReactElement {
  return (
    <div className="section">
      <div className="section-header">
        <span className="section-title">Recent Tasks</span>
      </div>
      <div className="section-body">
        {tasksLoading ? (
          <div style={{ color: "var(--text-muted)", fontSize: 12 }}>
            Loading tasks…
          </div>
        ) : !status.http_reachable ? (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            Maxwell-Daemon offline — no task history
          </div>
        ) : tasks.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            No tasks yet
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Status</th>
                <th>Repo</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td>{(task.id || "").slice(0, 8)}</td>
                  <td>{task.status || "—"}</td>
                  <td>{task.repo || "—"}</td>
                  <td>
                    {task.created_at
                      ? task.created_at.slice(0, 16).replace("T", " ")
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
