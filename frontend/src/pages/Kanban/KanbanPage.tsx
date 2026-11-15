/**
 * KanbanPage — read-only work-item board (epic #4317, issue #4322).
 *
 * Cards come from GET /api/kanban/github when deployed (#4319); until then the
 * page renders the hermetic fixture board so UI work does not block ingestion.
 */
import { useState } from "react";
import { TimeAgo } from "../../primitives/TimeAgo";
import { TouchButton } from "../../primitives/TouchButton";
import { KanbanBoard } from "./KanbanBoard";
import { DEFAULT_REPO, useKanbanBoard } from "./kanbanApi";
import "./kanban.css";

export function KanbanPage() {
  const [repo, setRepo] = useState(DEFAULT_REPO);
  const { data, loading, error, usedFixture, reload } = useKanbanBoard(repo);

  return (
    <div className="kanban-page">
      <header className="kanban-page__header">
        <div>
          <h2 className="kanban-page__title">Kanban</h2>
          <p className="kanban-page__lede">
            Read-only projection of open work from GitHub, claims, and holds. No card edits on the board.
          </p>
        </div>
        <div className="kanban-page__meta">
          <label className="staff-muted">
            Repo{" "}
            <input
              aria-label="Repository name"
              className="form-input"
              value={repo}
              onChange={(e) => setRepo(e.target.value.trim())}
              style={{ width: "10rem" }}
            />
          </label>
          <TouchButton disabled={loading} onClick={reload}>
            Refresh
          </TouchButton>
        </div>
      </header>

      {usedFixture ? (
        <p className="kanban-page__fixture" role="status" data-testid="kanban-fixture-banner">
          Demo board — live sync starts when GET /api/kanban/github is deployed (#4319).
        </p>
      ) : null}

      {error ? (
        <p className="staff-error" role="alert">
          {error}
        </p>
      ) : null}

      {loading && !data ? <p className="staff-muted">Loading board…</p> : null}

      {data ? (
        <p className="kanban-page__meta" role="status">
          Last sync <TimeAgo iso={data.last_sync} /> · {data.sync_mode} · {data.items.length} cards
        </p>
      ) : null}

      {data ? <KanbanBoard board={data} /> : null}
    </div>
  );
}

export default KanbanPage;
