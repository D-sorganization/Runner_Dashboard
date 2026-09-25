/**
 * CoverageDetails — how much of a repo's open work its charter accounts for,
 * with the untracked issues/PRs the fleet curator should place (deprecate,
 * integrate, implement or track). Links are GitHub URLs from the API.
 */
import React from "react";
import type { ProjectCoverage } from "./types";

export function CoverageDetails({
  coverage,
  error,
}: {
  coverage?: ProjectCoverage | null;
  error?: string;
}): React.ReactElement | null {
  if (coverage === undefined && !error) return null;
  if (!coverage) {
    return (
      <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
        Open-work coverage unavailable{error ? ` — ${error}` : ""}.
      </p>
    );
  }
  return (
    <div style={{ marginTop: 8, fontSize: 13 }}>
      <strong>Open work</strong>{" "}
      <span>
        {coverage.percent_tracked}% of {coverage.open_items} open items tracked
      </span>
      {coverage.untracked_count > 0 && (
        <details style={{ marginTop: 4 }}>
          <summary>Untracked work ({coverage.untracked_count})</summary>
          <ul style={{ margin: "4px 0 0 18px", padding: 0 }}>
            {coverage.untracked.map((item) => (
              <li key={item.number}>
                <a href={item.url} target="_blank" rel="noreferrer">
                  #{item.number} {item.title}
                </a>
                {item.kind === "pr" ? " (PR)" : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
