import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";

interface WslDistribution {
  name: string;
  path: string;
  vhdx_bytes: number | null;
  sparse: boolean | null;
  is_current: boolean;
  fs_used_bytes: number | null;
  fs_total_bytes: number | null;
  slack_bytes: number | null;
  findings: string[];
}

interface WslFstrim {
  timer_active?: boolean | null;
  last_trigger?: string | null;
  last_result?: string | null;
}

interface WslDiskStatusResponse {
  distributions: WslDistribution[];
  fstrim: WslFstrim;
  current_distro: string | null;
  generated_at?: string;
}

const CELL_STYLE: React.CSSProperties = {
  padding: "0.5rem 0.75rem",
  color: "var(--text-secondary, #8b949e)",
};

function formatGiB(bytes: number | null | undefined): string {
  if (
    bytes === null ||
    bytes === undefined ||
    typeof bytes !== "number" ||
    isNaN(bytes)
  ) {
    return "—";
  }
  const gib = bytes / (1024 * 1024 * 1024);
  return `${gib.toFixed(1)} GiB`;
}

function formatSparse(sparse: boolean | null | undefined): string {
  if (sparse === true) return "Yes";
  if (sparse === false) return "No";
  return "Unknown";
}

function formatFinding(distroName: string, finding: string): string {
  if (finding === "not_sparse") {
    return `${distroName}: VHDX is not sparse, so space freed inside WSL is not returned to Windows.`;
  }
  if (finding === "fstrim_timer_inactive") {
    return "fstrim.timer is not active on this node.";
  }
  return finding;
}

function formatFstrim(fstrim?: WslFstrim | null): string {
  const timerStatus =
    fstrim?.timer_active === true
      ? "active"
      : fstrim?.timer_active === false
        ? "inactive"
        : "unknown";
  const lastRun = fstrim?.last_trigger || "never";
  const result = fstrim?.last_result || "unknown";
  return `fstrim timer: ${timerStatus} · last run ${lastRun} (${result})`;
}

export function OperationsWslDiskCard(): React.ReactElement {
  const [data, setData] = useState<WslDiskStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = useCallback(() => {
    setLoading(true);
    setError(null);
    legacyFetch("/api/diagnostics/wsl-disk")
      .then((res) => {
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        return res.json() as Promise<WslDiskStatusResponse>;
      })
      .then((json) => {
        setData(json);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : "Failed to load WSL disk status",
        );
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const findings = (data?.distributions ?? []).flatMap((distro) =>
    (distro.findings || []).map((code, idx) => ({
      key: `${distro.name}-${code}-${idx}`,
      text: formatFinding(distro.name, code),
    })),
  );

  return (
    <section
      id="wsl-disk"
      aria-labelledby="heading-wsl-disk"
      style={{
        padding: "1rem",
        marginBottom: "1rem",
        borderRadius: "6px",
        background: "var(--bg-tertiary, #21262d)",
        border: "1px solid var(--border-subtle, #30363d)",
      }}
    >
      <h3
        id="heading-wsl-disk"
        style={{
          fontSize: "1rem",
          fontWeight: 600,
          margin: "0 0 0.75rem 0",
          color: "var(--text-primary, #c9d1d9)",
        }}
      >
        WSL disk
      </h3>

      {loading && (
        <div
          style={{ fontSize: "0.875rem", color: "var(--text-muted, #8b949e)" }}
        >
          Checking WSL disks…
        </div>
      )}

      {error && !loading && (
        <div
          role="alert"
          style={{
            padding: "0.75rem 1rem",
            borderRadius: "6px",
            background: "var(--bg-danger-subtle, rgba(248,81,73,0.1))",
            border: "1px solid var(--border-danger, #f85149)",
            color: "var(--text-danger, #f85149)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "0.5rem",
          }}
        >
          <span style={{ fontSize: "0.8125rem" }}>
            Failed to load WSL disk status: {error}
          </span>
          <button
            type="button"
            onClick={fetchStatus}
            style={{
              padding: "0.25rem 0.6rem",
              fontSize: "0.75rem",
              fontWeight: 600,
              borderRadius: "4px",
              cursor: "pointer",
              background: "var(--bg-tertiary, #21262d)",
              color: "var(--text-primary, #c9d1d9)",
              border: "1px solid var(--border-color, #30363d)",
            }}
          >
            Retry
          </button>
        </div>
      )}

      {!loading &&
        !error &&
        (!data?.distributions || data.distributions.length === 0) && (
          <div
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted, #8b949e)",
            }}
          >
            No WSL distributions found (not a Windows host?)
          </div>
        )}

      {!loading &&
        !error &&
        data &&
        data.distributions &&
        data.distributions.length > 0 && (
          <>
            <table
              aria-label="WSL distributions"
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: "0.8125rem",
                textAlign: "left",
                marginBottom: "0.75rem",
              }}
            >
              <thead>
                <tr
                  style={{
                    borderBottom: "1px solid var(--border-subtle, #30363d)",
                    color: "var(--text-muted, #8b949e)",
                  }}
                >
                  <th style={{ padding: "0.5rem 0.75rem" }}>Name</th>
                  <th style={{ padding: "0.5rem 0.75rem" }}>VHDX size</th>
                  <th style={{ padding: "0.5rem 0.75rem" }}>Used inside WSL</th>
                  <th style={{ padding: "0.5rem 0.75rem" }}>Slack</th>
                  <th style={{ padding: "0.5rem 0.75rem" }}>Sparse</th>
                </tr>
              </thead>
              <tbody>
                {data.distributions.map((distro) => {
                  const displayName = distro.is_current
                    ? `${distro.name} (this node)`
                    : distro.name;
                  const usedFormatted = distro.is_current
                    ? formatGiB(distro.fs_used_bytes)
                    : "—";
                  const slackFormatted = formatGiB(distro.slack_bytes);
                  const sparseFormatted = formatSparse(distro.sparse);
                  return (
                    <tr
                      key={distro.name}
                      style={{
                        borderBottom: "1px solid var(--border-subtle, #30363d)",
                      }}
                    >
                      <td
                        style={{
                          padding: "0.5rem 0.75rem",
                          fontWeight: 500,
                          color: "var(--text-primary, #c9d1d9)",
                        }}
                      >
                        {displayName}
                      </td>
                      <td style={CELL_STYLE}>{formatGiB(distro.vhdx_bytes)}</td>
                      <td style={CELL_STYLE}>{usedFormatted}</td>
                      <td style={CELL_STYLE}>{slackFormatted}</td>
                      <td style={CELL_STYLE}>{sparseFormatted}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            <div
              style={{
                fontSize: "0.8125rem",
                color: "var(--text-muted, #8b949e)",
                marginBottom: "0.5rem",
              }}
            >
              {formatFstrim(data.fstrim)}
            </div>

            {findings.length > 0 && (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "0.25rem",
                  marginBottom: "0.5rem",
                }}
              >
                {findings.map((findingItem) => (
                  <div
                    key={findingItem.key}
                    style={{
                      fontSize: "0.8125rem",
                      color: "var(--text-secondary, #8b949e)",
                    }}
                  >
                    {findingItem.text}
                  </div>
                ))}
              </div>
            )}

            <p
              style={{
                fontSize: "0.75rem",
                color: "var(--text-muted, #8b949e)",
                margin: "0.5rem 0 0 0",
              }}
            >
              See docs/runbooks/wsl-vhdx-compaction.md for the owner-approved
              manual procedure.
            </p>
          </>
        )}
    </section>
  );
}
