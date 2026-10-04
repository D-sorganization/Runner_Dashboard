// FleetMergeSettingsPanel.tsx – Fleet merge queue and branch protection drift status (RD#1850).

import { useState, type CSSProperties } from "react";
import { Badge } from "../../primitives/Badge";
import { Collapse } from "../../components/Collapse";
import type { FleetMergeSettingsPayload } from "./types";

const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
  marginTop: 8,
};

const thStyle: CSSProperties = {
  textAlign: "left",
  padding: "8px 12px",
  borderBottom: "1px solid var(--border)",
  color: "var(--text-muted)",
  fontWeight: 600,
};

const tdStyle: CSSProperties = {
  padding: "8px 12px",
  borderBottom: "1px solid var(--border)",
  verticalAlign: "top",
};

export function FleetMergeSettingsPanel() {
  const [data, setData] = useState<FleetMergeSettingsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchSettings = () => {
    setLoading(true);
    fetch("/api/queue/merge-settings")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: FleetMergeSettingsPayload) => {
        if (json && Array.isArray(json.results)) {
          setData(json);
          setError(null);
        }
      })
      .catch((err) => {
        setError(String(err));
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const isAllGreen = data?.status === "pass";

  return (
    <Collapse
      title="Fleet Merge Settings (RM#1900 / RD#1850)"
      badge={
        loading
          ? "Checking…"
          : error
            ? "Error"
            : isAllGreen
              ? "All Green"
              : data
                ? "Drift Detected"
                : null
      }
      defaultOpen={false}
    >
      <div style={{ padding: "8px 0" }}>
        <div style={{ marginBottom: 12 }}>
          <button
            className="btn"
            onClick={fetchSettings}
            disabled={loading}
          >
            {loading ? <span className="spinner" style={{ marginRight: 6 }} /> : null}
            Check Fleet Settings
          </button>
        </div>
        {error && (
          <div style={{ color: "var(--accent-red)", marginBottom: 8, fontSize: 13 }}>
            Failed to load fleet merge settings: {error}
          </div>
        )}
        {data && Array.isArray(data.results) && (
          <table style={tableStyle} aria-label="Fleet Merge Settings Table">
            <thead>
              <tr>
                <th style={thStyle}>Repository</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Findings</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((row) => (
                <tr key={row.repo}>
                  <td style={{ ...tdStyle, fontWeight: 500 }}>{row.repo}</td>
                  <td style={tdStyle}>
                    {row.status === "pass" ? (
                      <Badge tone="success">PASS</Badge>
                    ) : (
                      <Badge tone="danger">DRIFT</Badge>
                    )}
                  </td>
                  <td style={tdStyle}>
                    {row.findings.length === 0 ? (
                      <span style={{ color: "var(--text-muted)" }}>Configured correctly (strict off, merge queue active)</span>
                    ) : (
                      <ul style={{ margin: 0, paddingLeft: 18, color: "var(--accent-red)" }}>
                        {row.findings.map((f, i) => (
                          <li key={i}>{f}</li>
                        ))}
                      </ul>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Collapse>
  );
}

export default FleetMergeSettingsPanel;
