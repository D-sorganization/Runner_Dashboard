/**
 * AssessmentHistory — a repository's assessment scores and a "Request
 * assessment" action on its Projects card (#1338, SC-G6: split out of the
 * retired Assessments tab). Scores come from `GET /api/assessments/scores` via
 * the page; the request is the existing `assessment.run` Staff request
 * (Jules-Assess-Repo.yml), submitted by the page so this stays presentational.
 */
import React from "react";
import type { AssessmentScore } from "./types";

export interface AssessmentHistoryProps {
  repo: string;
  /** This repo's scores; any order (shown newest first). */
  entries: AssessmentScore[];
  /** True while an assessment request is being submitted. */
  requesting: boolean;
  /** Result line from the last request, if any. */
  notice?: string;
  onRequest: (repo: string, provider: string) => void;
}

const PROVIDERS: { id: string; label: string }[] = [
  { id: "jules_api", label: "Jules" },
  { id: "codex", label: "Codex" },
  { id: "claude", label: "Claude" },
];

function formatAssessmentScore(entry: AssessmentScore): string {
  const value = entry.score;
  if (value == null || value === "") return "—";
  if (typeof value === "number") {
    return value <= 1 ? `${Math.round(value * 100)}%` : String(value);
  }
  return value;
}

function dateKey(entry: AssessmentScore): number {
  const value = entry.date;
  if (typeof value === "number") return value * 1000;
  if (!value) return 0;
  const parsed = Date.parse(String(value));
  return Number.isNaN(parsed) ? 0 : parsed;
}

function formatDate(entry: AssessmentScore): string {
  const value = entry.date;
  if (!value) return "—";
  if (typeof value === "number") return new Date(value * 1000).toLocaleDateString();
  return String(value).slice(0, 10);
}

export function AssessmentHistory({
  repo,
  entries,
  requesting,
  notice,
  onRequest,
}: AssessmentHistoryProps): React.ReactElement {
  const [provider, setProvider] = React.useState("jules_api");
  const sorted = [...entries].sort((a, b) => dateKey(b) - dateKey(a));
  return (
    <div className="projects-assessments" style={{ marginTop: 8, fontSize: 13 }}>
      {sorted.length === 0 ? (
        <div>
          <strong>Assessments</strong>
          <span style={{ color: "var(--text-secondary)" }}>
            {" "}
            — No assessment recorded.
          </span>
        </div>
      ) : (
        <details>
          <summary>
            <strong>Assessments ({sorted.length})</strong> — latest{" "}
            <span className="assessment-score">{formatAssessmentScore(sorted[0])}</span>
          </summary>
          <ul className="projects-assessment-history" style={{ margin: "4px 0 0 18px", padding: 0 }}>
            {sorted.map((entry, i) => (
              <li key={`${String(entry.date)}-${i}`}>
                <span className="assessment-score">{formatAssessmentScore(entry)}</span>{" "}
                <span style={{ color: "var(--text-muted)" }}>
                  {entry.provider || "provider unknown"} · {formatDate(entry)}
                </span>
                {" — "}
                <span data-testid="assessment-summary">{entry.summary || "No summary captured."}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 6, alignItems: "center" }}>
        <select
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          aria-label={`Assessment provider for ${repo}`}
        >
          {PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn"
          disabled={requesting}
          onClick={() => onRequest(repo, provider)}
          aria-label={`Request assessment for ${repo}`}
        >
          {requesting ? "Submitting…" : "Request assessment"}
        </button>
      </div>
      {notice && (
        <p role="status" style={{ marginTop: 6, fontSize: 12, color: "var(--text-secondary)" }}>
          {notice}
        </p>
      )}
    </div>
  );
}
