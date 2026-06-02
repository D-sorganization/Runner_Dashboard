/**
 * Principals.tsx — the "Principals" admin tab, extracted from the legacy
 * `App.tsx` monolith into a typed module (decomposition #836, related #834).
 *
 * Behaviour is preserved 1:1 from the legacy `PrincipalsTab`:
 *  - loads registered principals + active service tokens on mount;
 *  - edits a principal's quotas via `PATCH /api/admin/principals/:id/quota`;
 *  - mints a service token via `POST /api/admin/principals/:id/token`
 *    (one-time reveal), and revokes via `DELETE /api/admin/tokens/:hash`;
 *  - surfaces a single error banner.
 *
 * LoD: the component talks only to the typed API shapes below through the
 * shared `legacyFetch` (adds the CSRF header); callers pass no props.
 * Orthogonality: an admin 5xx surfaces inline and never touches Fleet.
 */
import React from "react";
import { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../lib/api";
import { ServerGlyph } from "./decompIcons";

interface Quotas {
  max_runners: number | string;
  agent_spend_usd_day: number | string;
  local_app_slots: number | string;
}

interface Principal {
  id: string;
  type: string;
  roles: string[];
  quotas: Quotas;
}

interface ServiceToken {
  hash: string;
  principal_id: string;
  name?: string | null;
  created_at: string;
}

interface EditingQuota {
  principalId: string;
  quotas: Quotas;
}

export function PrincipalsTab(): React.ReactElement {
  const [principals, setPrincipals] = useState<Principal[]>([]);
  const [tokens, setTokens] = useState<ServiceToken[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editingQuota, setEditingQuota] = useState<EditingQuota | null>(null);
  const [mintingToken, setMintingToken] = useState<string | null>(null);
  const [createdToken, setCreatedToken] = useState<string | null>(null);

  const loadData = useCallback(() => {
    legacyFetch("/api/admin/principals")
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load principals");
        return r.json() as Promise<{ principals: Principal[] }>;
      })
      .then((data) => setPrincipals(data.principals))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e)),
      );

    legacyFetch("/api/admin/tokens")
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load tokens");
        return r.json() as Promise<{ tokens: ServiceToken[] }>;
      })
      .then((data) => setTokens(data.tokens))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e)),
      );
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const saveQuota = useCallback(
    (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!editingQuota) return;
      const q = editingQuota.quotas;
      legacyFetch(
        "/api/admin/principals/" + editingQuota.principalId + "/quota",
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            max_runners: parseInt(String(q.max_runners), 10),
            agent_spend_usd_day: parseFloat(String(q.agent_spend_usd_day)),
            local_app_slots: parseInt(String(q.local_app_slots), 10),
          }),
        },
      )
        .then((r) => {
          if (!r.ok) throw new Error("Failed to update quota");
          setEditingQuota(null);
          loadData();
        })
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : String(err)),
        );
    },
    [editingQuota, loadData],
  );

  const doMintToken = useCallback(
    (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      if (!mintingToken) return;
      const form = e.currentTarget;
      const name = (form.elements.namedItem("name") as HTMLInputElement).value;
      const exp = (form.elements.namedItem("expires") as HTMLInputElement).value;
      const body: { name: string; expires_in_days?: number } = { name };
      if (exp) body.expires_in_days = parseInt(exp, 10);

      legacyFetch("/api/admin/principals/" + mintingToken + "/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then((r) => {
          if (!r.ok) throw new Error("Failed to mint token");
          return r.json() as Promise<{ token: string }>;
        })
        .then((data) => {
          setMintingToken(null);
          setCreatedToken(data.token);
          loadData();
        })
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : String(err)),
        );
    },
    [mintingToken, loadData],
  );

  const revokeToken = useCallback(
    (hash: string) => {
      if (!window.confirm("Are you sure you want to revoke this token?")) return;
      legacyFetch("/api/admin/tokens/" + hash, { method: "DELETE" })
        .then((r) => {
          if (!r.ok) throw new Error("Failed to revoke token");
          loadData();
        })
        .catch((err: unknown) =>
          setError(err instanceof Error ? err.message : String(err)),
        );
    },
    [loadData],
  );

  const modalOverlayStyle: React.CSSProperties = {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: "rgba(0,0,0,0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  };
  const labelStyle: React.CSSProperties = {
    display: "block",
    marginBottom: "4px",
    fontSize: "12px",
    color: "var(--text-secondary)",
  };
  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "8px",
    background: "var(--bg-secondary)",
    border: "1px solid var(--border)",
    color: "white",
    borderRadius: "4px",
  };

  return (
    <div className="section" style={{ marginTop: "16px" }}>
      {error && (
        <div
          className="error-banner"
          style={{
            marginBottom: "16px",
            padding: "12px",
            background: "rgba(255,0,0,0.1)",
            borderLeft: "4px solid red",
            color: "var(--text-primary)",
          }}
        >
          {error}
        </div>
      )}

      {createdToken && (
        <div
          className="glass-card"
          style={{
            padding: "20px",
            marginBottom: "20px",
            border: "1px solid var(--accent-green)",
            background: "rgba(46, 160, 67, 0.1)",
          }}
        >
          <h3 style={{ color: "var(--accent-green)", marginBottom: "8px" }}>
            Token Successfully Created!
          </h3>
          <p style={{ marginBottom: "12px" }}>
            Please copy this token now. You will not be able to see it again.
          </p>
          <div
            style={{
              background: "var(--bg-secondary)",
              padding: "12px",
              borderRadius: "6px",
              fontFamily: "monospace",
              fontSize: "16px",
              wordBreak: "break-all",
              border: "1px solid var(--border)",
            }}
          >
            {createdToken}
          </div>
          <button
            className="btn"
            style={{ marginTop: "12px" }}
            onClick={() => setCreatedToken(null)}
            aria-label="Dismiss token"
          >
            Dismiss
          </button>
        </div>
      )}

      {editingQuota && (
        <div style={modalOverlayStyle}>
          <div
            className="glass-card"
            style={{ padding: "24px", width: "400px", maxWidth: "90%" }}
          >
            <h3 style={{ marginBottom: "16px" }}>
              {"Edit Quota: " + editingQuota.principalId}
            </h3>
            <form
              onSubmit={saveQuota}
              style={{ display: "flex", flexDirection: "column", gap: "12px" }}
            >
              <div>
                <label style={labelStyle}>Max Runners</label>
                <input
                  type="number"
                  value={editingQuota.quotas.max_runners}
                  onChange={(e) =>
                    setEditingQuota({
                      ...editingQuota,
                      quotas: {
                        ...editingQuota.quotas,
                        max_runners: e.target.value,
                      },
                    })
                  }
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Agent Spend (USD/Day)</label>
                <input
                  type="number"
                  step="0.01"
                  value={editingQuota.quotas.agent_spend_usd_day}
                  onChange={(e) =>
                    setEditingQuota({
                      ...editingQuota,
                      quotas: {
                        ...editingQuota.quotas,
                        agent_spend_usd_day: e.target.value,
                      },
                    })
                  }
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Local App Slots</label>
                <input
                  type="number"
                  value={editingQuota.quotas.local_app_slots}
                  onChange={(e) =>
                    setEditingQuota({
                      ...editingQuota,
                      quotas: {
                        ...editingQuota.quotas,
                        local_app_slots: e.target.value,
                      },
                    })
                  }
                  style={inputStyle}
                />
              </div>
              <div
                style={{
                  display: "flex",
                  gap: "8px",
                  justifyContent: "flex-end",
                  marginTop: "16px",
                }}
              >
                <button
                  type="button"
                  className="btn"
                  onClick={() => setEditingQuota(null)}
                  aria-label="Cancel editing quota"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn"
                  style={{
                    background: "var(--accent-blue)",
                    color: "white",
                    borderColor: "var(--accent-blue)",
                  }}
                  aria-label="Save quota settings"
                >
                  Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {mintingToken && (
        <div style={modalOverlayStyle}>
          <div
            className="glass-card"
            style={{ padding: "24px", width: "400px", maxWidth: "90%" }}
          >
            <h3 style={{ marginBottom: "16px" }}>
              {"Mint Token: " + mintingToken}
            </h3>
            <form
              onSubmit={doMintToken}
              style={{ display: "flex", flexDirection: "column", gap: "12px" }}
            >
              <div>
                <label style={labelStyle}>Token Name</label>
                <input
                  name="name"
                  type="text"
                  placeholder="e.g., prod-deployment-script"
                  required
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Expires in Days (Optional)</label>
                <input
                  name="expires"
                  type="number"
                  placeholder="e.g., 30"
                  style={inputStyle}
                />
              </div>
              <div
                style={{
                  display: "flex",
                  gap: "8px",
                  justifyContent: "flex-end",
                  marginTop: "16px",
                }}
              >
                <button
                  type="button"
                  className="btn"
                  onClick={() => setMintingToken(null)}
                  aria-label="Cancel minting token"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn"
                  style={{
                    background: "var(--accent-green)",
                    color: "white",
                    borderColor: "var(--accent-green)",
                  }}
                  aria-label="Mint access token"
                >
                  Mint
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div
        className="section-header"
        style={{ background: "var(--grad-fair)", color: "white" }}
      >
        <div className="section-title">
          <ServerGlyph size={16} />
          Registered Principals
        </div>
        <span
          className="section-badge"
          style={{ background: "rgba(255,255,255,0.2)", color: "white" }}
        >
          {principals.length}
        </span>
      </div>
      <div className="section-body" style={{ padding: "0" }}>
        <table
          className="data-table"
          style={{ width: "100%", borderCollapse: "collapse" }}
        >
          <thead>
            <tr
              style={{
                borderBottom: "1px solid var(--border)",
                textAlign: "left",
                background: "var(--bg-secondary)",
              }}
            >
              <th style={{ padding: "12px" }}>ID</th>
              <th style={{ padding: "12px" }}>Type</th>
              <th style={{ padding: "12px" }}>Roles</th>
              <th style={{ padding: "12px" }}>Quotas</th>
              <th style={{ padding: "12px", textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {principals.map((p) => (
              <tr key={p.id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td
                  style={{
                    padding: "12px",
                    fontWeight: "600",
                    color: "var(--accent-blue)",
                  }}
                >
                  {p.id}
                </td>
                <td style={{ padding: "12px" }}>
                  <span
                    className="section-badge"
                    style={{
                      background:
                        p.type === "bot"
                          ? "rgba(188, 140, 255, 0.15)"
                          : "rgba(88, 166, 255, 0.15)",
                      color:
                        p.type === "bot"
                          ? "var(--accent-purple)"
                          : "var(--accent-blue)",
                    }}
                  >
                    {p.type}
                  </span>
                </td>
                <td style={{ padding: "12px" }}>
                  {p.roles.map((r) => (
                    <span
                      key={r}
                      className="section-badge"
                      style={{
                        marginRight: "4px",
                        background: "rgba(255,255,255,0.1)",
                        color: "var(--text-secondary)",
                      }}
                    >
                      {r}
                    </span>
                  ))}
                </td>
                <td
                  style={{
                    padding: "12px",
                    fontSize: "12px",
                    color: "var(--text-secondary)",
                  }}
                >
                  <div>
                    Runners:{" "}
                    <strong style={{ color: "var(--text-primary)" }}>
                      {p.quotas.max_runners}
                    </strong>
                  </div>
                  <div>
                    Spend: $
                    <strong style={{ color: "var(--text-primary)" }}>
                      {parseFloat(String(p.quotas.agent_spend_usd_day)).toFixed(2)}
                    </strong>
                    /day
                  </div>
                  <div>
                    App Slots:{" "}
                    <strong style={{ color: "var(--text-primary)" }}>
                      {p.quotas.local_app_slots}
                    </strong>
                  </div>
                </td>
                <td style={{ padding: "12px", textAlign: "right" }}>
                  <button
                    className="btn"
                    style={{
                      marginRight: "8px",
                      fontSize: "12px",
                      padding: "4px 8px",
                    }}
                    onClick={() =>
                      setEditingQuota({
                        principalId: p.id,
                        quotas: { ...p.quotas },
                      })
                    }
                  >
                    Edit Quota
                  </button>
                  {p.type === "bot" && (
                    <button
                      className="btn"
                      style={{
                        fontSize: "12px",
                        padding: "4px 8px",
                        background: "rgba(188, 140, 255, 0.1)",
                        color: "var(--accent-purple)",
                        borderColor: "var(--accent-purple)",
                      }}
                      onClick={() => setMintingToken(p.id)}
                    >
                      Mint Token
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div
        className="section-header"
        style={{
          background: "var(--glass-bg)",
          borderTop: "1px solid var(--border)",
          color: "var(--text-primary)",
          marginTop: "24px",
        }}
      >
        <div className="section-title">
          <ServerGlyph size={16} />
          Active Service Tokens
        </div>
        <span
          className="section-badge"
          style={{
            background: "rgba(255,255,255,0.1)",
            color: "var(--text-secondary)",
          }}
        >
          {tokens.length}
        </span>
      </div>
      <div className="section-body" style={{ padding: "0" }}>
        <table
          className="data-table"
          style={{ width: "100%", borderCollapse: "collapse" }}
        >
          <thead>
            <tr
              style={{
                borderBottom: "1px solid var(--border)",
                textAlign: "left",
                background: "var(--bg-secondary)",
              }}
            >
              <th style={{ padding: "12px" }}>Principal</th>
              <th style={{ padding: "12px" }}>Name</th>
              <th style={{ padding: "12px" }}>Token Hash</th>
              <th style={{ padding: "12px" }}>Created</th>
              <th style={{ padding: "12px", textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {tokens.length === 0 && (
              <tr>
                <td
                  colSpan={5}
                  style={{
                    padding: "24px",
                    textAlign: "center",
                    color: "var(--text-secondary)",
                  }}
                >
                  No active service tokens found.
                </td>
              </tr>
            )}
            {tokens.map((t) => (
              <tr key={t.hash} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "12px", fontWeight: "600" }}>
                  {t.principal_id}
                </td>
                <td style={{ padding: "12px", color: "var(--text-secondary)" }}>
                  {t.name || "-"}
                </td>
                <td
                  style={{
                    padding: "12px",
                    fontFamily: "monospace",
                    fontSize: "12px",
                    color: "var(--text-secondary)",
                  }}
                >
                  {t.hash.substring(0, 16) + "..."}
                </td>
                <td
                  style={{
                    padding: "12px",
                    fontSize: "12px",
                    color: "var(--text-secondary)",
                  }}
                >
                  {new Date(t.created_at).toLocaleString()}
                </td>
                <td style={{ padding: "12px", textAlign: "right" }}>
                  <button
                    className="btn btn-red"
                    style={{ fontSize: "12px", padding: "4px 8px" }}
                    onClick={() => revokeToken(t.hash)}
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
