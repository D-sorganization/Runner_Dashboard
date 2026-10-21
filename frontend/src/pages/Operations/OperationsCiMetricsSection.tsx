import React, { useCallback, useEffect, useState } from "react";
import { TABLE_FRAME_STYLE } from "./tableFrame";

const QUEUE_P90_TARGET_SECONDS = 10 * 60;

interface CiMetricsRow {
  key: string;
  job_count: number;
  queue_count: number;
  execute_count: number;
  queue_p50_seconds: number | null;
  queue_p95_seconds: number | null;
  execute_p50_seconds: number | null;
  execute_p95_seconds: number | null;
}

export interface CiMetricsResponse {
  generated_at: string;
  window: { days: number; start: string; end: string };
  group_by: "repo" | "event" | "label" | "host";
  sample: { jobs: number; queue: number; execution: number };
  coverage: {
    complete: boolean;
    organizations_reported: number;
    partial_reasons: string[];
    caps: string[];
    incomplete_runs: number;
  };
  labels_are_non_additive: boolean;
  queue_semantics: string;
  rows: CiMetricsRow[];
}

export interface OperationsCiMetricsSectionProps {
  initialData?: CiMetricsResponse;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNonnegativeInteger(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= 0;
}

function isPercentile(value: unknown): value is number | null {
  return value === null || (isFiniteNumber(value) && value >= 0);
}

function hasValidPercentiles(
  count: number,
  p50: number | null,
  p95: number | null,
): boolean {
  return count === 0
    ? p50 === null && p95 === null
    : p50 !== null && p95 !== null && p50 <= p95;
}

function isValidMetricsRow(value: unknown): value is CiMetricsRow {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<CiMetricsRow>;
  return (
    typeof row.key === "string" &&
    isNonnegativeInteger(row.job_count) &&
    isNonnegativeInteger(row.queue_count) &&
    isNonnegativeInteger(row.execute_count) &&
    row.queue_count <= row.job_count &&
    row.execute_count <= row.job_count &&
    isPercentile(row.queue_p50_seconds) &&
    isPercentile(row.queue_p95_seconds) &&
    isPercentile(row.execute_p50_seconds) &&
    isPercentile(row.execute_p95_seconds) &&
    hasValidPercentiles(
      row.queue_count,
      row.queue_p50_seconds,
      row.queue_p95_seconds,
    ) &&
    hasValidPercentiles(
      row.execute_count,
      row.execute_p50_seconds,
      row.execute_p95_seconds,
    )
  );
}

function parseCiMetricsResponse(value: unknown): CiMetricsResponse {
  if (!value || typeof value !== "object") {
    throw new Error("Invalid CI metrics response");
  }
  const payload = value as Partial<CiMetricsResponse>;
  const validRows =
    Array.isArray(payload.rows) && payload.rows.every(isValidMetricsRow);
  if (
    !payload.window ||
    !isFiniteNumber(payload.window.days) ||
    !payload.sample ||
    !isNonnegativeInteger(payload.sample.jobs) ||
    !isNonnegativeInteger(payload.sample.queue) ||
    !isNonnegativeInteger(payload.sample.execution) ||
    payload.sample.queue > payload.sample.jobs ||
    payload.sample.execution > payload.sample.jobs ||
    !payload.coverage ||
    typeof payload.coverage.complete !== "boolean" ||
    !Array.isArray(payload.coverage.partial_reasons) ||
    !validRows
  ) {
    throw new Error("Invalid CI metrics response");
  }
  return payload as CiMetricsResponse;
}

function formatDuration(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder === 0 ? `${minutes}m` : `${minutes}m ${remainder}s`;
}

function queueSloStatus(row: CiMetricsRow): string {
  if (
    row.queue_count > 0 &&
    row.queue_p95_seconds !== null &&
    row.queue_p95_seconds <= QUEUE_P90_TARGET_SECONDS
  ) {
    return "P95 bound confirms P90 target";
  }
  if (
    row.queue_count > 0 &&
    row.queue_p50_seconds !== null &&
    row.queue_p50_seconds > QUEUE_P90_TARGET_SECONDS
  ) {
    return "P50 bound exceeds P90 target";
  }
  return "P90 status is indeterminate";
}

export function OperationsCiMetricsSection({
  initialData,
}: OperationsCiMetricsSectionProps): React.ReactElement {
  const [data, setData] = useState<CiMetricsResponse | null>(
    initialData ?? null,
  );
  const [loading, setLoading] = useState(initialData === undefined);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    fetch("/api/ci/metrics?days=14&group_by=repo", {
      signal,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<unknown>;
      })
      .then((payload) => setData(parseCiMetricsResponse(payload)))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === "AbortError")
          return;
        setError(
          cause instanceof Error ? cause.message : "Unable to load CI metrics",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (initialData !== undefined) {
      setData(initialData);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [initialData, load]);

  const retry = () => load();
  const partialCoverage = data && !data.coverage.complete;

  return (
    <section
      id="ci-metrics"
      aria-labelledby="heading-ci-metrics"
      style={{
        padding: "1.25rem",
        marginBottom: "1.5rem",
        borderRadius: "8px",
        background: "var(--bg-secondary, #161b22)",
        border: "1px solid var(--border-color, #30363d)",
      }}
    >
      <header style={{ marginBottom: "1rem" }}>
        <h2
          id="heading-ci-metrics"
          style={{ margin: "0 0 0.25rem", fontSize: "1.25rem" }}
        >
          CI Job Metrics
        </h2>
        <p style={{ color: "var(--text-muted, #8b949e)", margin: 0 }}>
          Queue wait and runner execution percentiles over the last 14 days.
        </p>
      </header>

      {loading && <p role="status">Loading CI metrics…</p>}
      {error && (
        <div role="alert">
          <p>Failed to load CI metrics: {error}</p>
          <button type="button" onClick={retry}>
            Retry
          </button>
        </div>
      )}
      {data && !loading && (
        <>
          <p>
            {data.sample.jobs} jobs · {data.sample.queue} queue samples ·{" "}
            {data.sample.execution} execution samples
          </p>
          {partialCoverage && (
            <p role="status">
              Collection coverage is partial ({data.coverage.incomplete_runs}{" "}
              incomplete runs):{" "}
              {data.coverage.partial_reasons.join(", ") ||
                "coverage is incomplete"}
              .
            </p>
          )}
          {data.labels_are_non_additive && (
            <p role="note">
              Label groups are non-additive; a job can appear under multiple
              labels.
            </p>
          )}
          <p role="note">
            Queue P90 target: 10 minutes (provisional SLO line). The API reports
            P50/P95, so each row shows a conservative bound instead of
            estimating P90.
          </p>
          <div style={TABLE_FRAME_STYLE}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                minWidth: "760px",
              }}
            >
              <thead>
                <tr>
                  <th scope="col">{data.group_by}</th>
                  <th scope="col">Jobs</th>
                  <th scope="col">Queue P50</th>
                  <th scope="col">Queue P95</th>
                  <th scope="col">Execute P50</th>
                  <th scope="col">Execute P95</th>
                  <th scope="col">Queue SLO</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.key}>
                    <th scope="row">{row.key}</th>
                    <td>{row.job_count}</td>
                    <td>{formatDuration(row.queue_p50_seconds)}</td>
                    <td>{formatDuration(row.queue_p95_seconds)}</td>
                    <td>{formatDuration(row.execute_p50_seconds)}</td>
                    <td>{formatDuration(row.execute_p95_seconds)}</td>
                    <td>{queueSloStatus(row)}</td>
                  </tr>
                ))}
                {data.rows.length === 0 && (
                  <tr>
                    <td colSpan={7}>No job timing samples in this window.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p role="note">{data.queue_semantics}</p>
        </>
      )}
    </section>
  );
}
