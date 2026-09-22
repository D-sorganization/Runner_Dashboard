/**
 * FeatureProgressBar — stacked shipped / in-progress / planned / parked bar for
 * one project card (issue #1199). Reuses the global `.progress-bar` /
 * `.progress-fill` styles so it matches the fleet capacity meters.
 */
import React from "react";
import type { FeatureProgress } from "./types";

const SEGMENTS: ReadonlyArray<{
  key: keyof FeatureProgress;
  label: string;
  fill: string;
}> = [
  { key: "shipped", label: "shipped", fill: "green" },
  { key: "in_progress", label: "in progress", fill: "blue" },
  { key: "planned", label: "planned", fill: "purple" },
  { key: "parked", label: "parked", fill: "yellow" },
];

export function FeatureProgressBar({
  progress,
}: {
  progress: FeatureProgress;
}): React.ReactElement {
  const total =
    progress.shipped +
    progress.in_progress +
    progress.planned +
    progress.parked;
  const summary = SEGMENTS.map((s) => `${progress[s.key]} ${s.label}`).join(
    ", ",
  );
  return (
    <div className="projects-progress">
      <div
        className="progress-bar projects-progress__bar"
        role="progressbar"
        aria-label={`Features: ${summary}`}
        aria-valuenow={progress.percent_shipped}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{ display: "flex" }}
      >
        {total > 0 &&
          SEGMENTS.map((s) =>
            progress[s.key] > 0 ? (
              <div
                key={s.key}
                className={`progress-fill ${s.fill}`}
                style={{
                  width: `${(100 * progress[s.key]) / total}%`,
                  borderRadius: 0,
                }}
                title={`${progress[s.key]} ${s.label}`}
              />
            ) : null,
          )}
      </div>
      <div
        className="projects-progress__legend"
        style={{ fontSize: 12, color: "var(--text-secondary)" }}
      >
        <strong>{progress.percent_shipped}% shipped</strong> · {summary}
      </div>
    </div>
  );
}
