/**
 * LabelGuide — Fleet tab section showing runner label taxonomy and routing guidance.
 *
 * Issue #757: workflow routing guidance for NVMe, HDD, Docker, and bulk labels.
 */

import React, { useCallback, useEffect, useState } from "react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LabelEntry {
  purpose: string;
  workload: string;
  avoid_for: string;
  runs_on_snippet: string;
}

interface WorkflowClass {
  description: string;
  recommended_labels: string[];
  forbidden_labels: string[];
}

interface LabelGuidanceResponse {
  taxonomy: Record<string, LabelEntry>;
  neutral_labels: string[];
  workflow_classes: Record<string, WorkflowClass>;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Copy-to-clipboard helper
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      aria-label={copied ? "Copied!" : "Copy snippet"}
      style={{
        marginLeft: "8px",
        padding: "2px 8px",
        fontSize: "11px",
        fontWeight: 500,
        cursor: "pointer",
        background: copied ? "var(--accent, #22c55e)" : "var(--surface-alt, #1e293b)",
        color: copied ? "#fff" : "var(--text-secondary, #94a3b8)",
        border: "1px solid var(--border, #334155)",
        borderRadius: "4px",
        transition: "background 0.2s, color 0.2s",
      }}
    >
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

// ---------------------------------------------------------------------------
// LabelGuide component
// ---------------------------------------------------------------------------

export function LabelGuide() {
  const [data, setData] = useState<LabelGuidanceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch("/api/runners/label-guidance")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<LabelGuidanceResponse>;
      })
      .then((json) => {
        if (!cancelled) {
          setData(json);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(String(err));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <section aria-label="Label Guide loading" style={{ padding: "16px" }}>
        <p style={{ color: "var(--text-secondary, #94a3b8)" }}>
          Loading label guidance…
        </p>
      </section>
    );
  }

  if (error || !data) {
    return (
      <section aria-label="Label Guide error" style={{ padding: "16px" }}>
        <p style={{ color: "var(--danger, #ef4444)" }}>
          Failed to load label guidance: {error ?? "unknown error"}
        </p>
      </section>
    );
  }

  const labelOrder = [
    "d-sorg-fleet-nvme",
    "d-sorg-fleet-fast-io",
    "d-sorg-fleet-docker",
    "d-sorg-fleet-bulk",
  ];

  const orderedTaxonomy = [
    ...labelOrder
      .filter((l) => l in data.taxonomy)
      .map((l) => [l, data.taxonomy[l]] as [string, LabelEntry]),
    ...Object.entries(data.taxonomy).filter(([l]) => !labelOrder.includes(l)),
  ];

  return (
    <section
      aria-label="Label Guide"
      style={{
        padding: "16px",
        fontFamily: "inherit",
        maxWidth: "960px",
      }}
    >
      <h2
        style={{
          fontSize: "16px",
          fontWeight: 700,
          marginBottom: "4px",
          color: "var(--text-primary, #f1f5f9)",
        }}
      >
        Runner Label Guide
      </h2>
      <p
        style={{
          fontSize: "13px",
          color: "var(--text-secondary, #94a3b8)",
          marginBottom: "20px",
        }}
      >
        Use these labels in{" "}
        <code style={{ fontSize: "12px" }}>runs-on</code> to route jobs to the
        correct runner tier. Neutral labels (
        {data.neutral_labels.map((l) => (
          <code key={l} style={{ fontSize: "12px", marginLeft: "4px" }}>
            {l}
          </code>
        ))}
        ) remain safe during the transition period.
      </p>

      {/* ── Taxonomy table ── */}
      <div
        style={{
          overflowX: "auto",
          borderRadius: "8px",
          border: "1px solid var(--border, #334155)",
          marginBottom: "24px",
        }}
      >
        <table
          style={{
            width: "100%",
            borderCollapse: "collapse",
            fontSize: "13px",
            color: "var(--text-primary, #f1f5f9)",
          }}
        >
          <thead>
            <tr
              style={{
                background: "var(--surface-alt, #1e293b)",
                textAlign: "left",
              }}
            >
              {["Label", "Use for", "Avoid for", "runs-on snippet"].map(
                (heading) => (
                  <th
                    key={heading}
                    style={{
                      padding: "10px 14px",
                      fontWeight: 600,
                      fontSize: "12px",
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                      color: "var(--text-secondary, #94a3b8)",
                      borderBottom: "1px solid var(--border, #334155)",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {heading}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {orderedTaxonomy.map(([label, info], idx) => (
              <tr
                key={label}
                style={{
                  background:
                    idx % 2 === 0
                      ? "var(--surface, #0f172a)"
                      : "var(--surface-alt, #1e293b)",
                  verticalAlign: "top",
                }}
              >
                <td style={{ padding: "10px 14px", whiteSpace: "nowrap" }}>
                  <code
                    data-testid={`label-name-${label}`}
                    style={{
                      fontSize: "12px",
                      background: "var(--surface-alt, #1e293b)",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      border: "1px solid var(--border, #334155)",
                    }}
                  >
                    {label}
                  </code>
                </td>
                <td
                  style={{
                    padding: "10px 14px",
                    color: "var(--text-primary, #f1f5f9)",
                    maxWidth: "220px",
                  }}
                >
                  {info.workload}
                </td>
                <td
                  style={{
                    padding: "10px 14px",
                    color: "var(--text-secondary, #94a3b8)",
                    maxWidth: "200px",
                  }}
                >
                  {info.avoid_for}
                </td>
                <td style={{ padding: "10px 14px", whiteSpace: "nowrap" }}>
                  <code
                    style={{
                      fontSize: "12px",
                      background: "var(--surface-alt, #1e293b)",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      border: "1px solid var(--border, #334155)",
                    }}
                  >
                    {info.runs_on_snippet}
                  </code>
                  <CopyButton text={info.runs_on_snippet} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Workflow class guidance ── */}
      {Object.keys(data.workflow_classes).length > 0 && (
        <>
          <h3
            style={{
              fontSize: "14px",
              fontWeight: 700,
              marginBottom: "12px",
              color: "var(--text-primary, #f1f5f9)",
            }}
          >
            Workflow Class Routing
          </h3>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
              gap: "12px",
              marginBottom: "16px",
            }}
          >
            {Object.entries(data.workflow_classes).map(([cls, info]) => (
              <div
                key={cls}
                style={{
                  background: "var(--surface-alt, #1e293b)",
                  border: "1px solid var(--border, #334155)",
                  borderRadius: "8px",
                  padding: "14px",
                }}
              >
                <div
                  style={{
                    fontWeight: 700,
                    fontSize: "13px",
                    marginBottom: "6px",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    color: "var(--text-primary, #f1f5f9)",
                  }}
                >
                  {cls}
                </div>
                <p
                  style={{
                    fontSize: "12px",
                    color: "var(--text-secondary, #94a3b8)",
                    marginBottom: "10px",
                    lineHeight: "1.5",
                  }}
                >
                  {info.description}
                </p>
                {info.recommended_labels.length > 0 && (
                  <div style={{ marginBottom: "6px" }}>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 600,
                        color: "var(--success, #22c55e)",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginRight: "6px",
                      }}
                    >
                      Recommended:
                    </span>
                    {info.recommended_labels.map((l) => (
                      <code
                        key={l}
                        style={{
                          fontSize: "11px",
                          background: "var(--surface, #0f172a)",
                          padding: "1px 5px",
                          borderRadius: "3px",
                          marginRight: "4px",
                          border: "1px solid var(--border, #334155)",
                          color: "var(--text-primary, #f1f5f9)",
                        }}
                      >
                        {l}
                      </code>
                    ))}
                  </div>
                )}
                {info.forbidden_labels.length > 0 && (
                  <div>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 600,
                        color: "var(--danger, #ef4444)",
                        textTransform: "uppercase",
                        letterSpacing: "0.05em",
                        marginRight: "6px",
                      }}
                    >
                      Forbidden:
                    </span>
                    {info.forbidden_labels.map((l) => (
                      <code
                        key={l}
                        style={{
                          fontSize: "11px",
                          background: "var(--surface, #0f172a)",
                          padding: "1px 5px",
                          borderRadius: "3px",
                          marginRight: "4px",
                          border: "1px solid var(--border, #334155)",
                          color: "var(--text-secondary, #94a3b8)",
                        }}
                      >
                        {l}
                      </code>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      <p
        style={{
          fontSize: "11px",
          color: "var(--text-secondary, #94a3b8)",
          marginTop: "8px",
        }}
      >
        Policy source:{" "}
        <code style={{ fontSize: "11px" }}>
          config/workflow_runner_routing_policy.json
        </code>
        {" · "}Last refreshed: {new Date(data.generated_at).toLocaleTimeString()}
      </p>
    </section>
  );
}
