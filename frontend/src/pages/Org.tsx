/**
 * Org.tsx — the "Organization" tab, extracted (behaviour-wise 1:1) from the
 * legacy `App.tsx` monolith as part of the decomposition epic (#836, pass 3).
 *
 * Lists every repository in the organization with name/description/language
 * search, sortable headline columns (Recent / PRs / Issues / Name), aggregate
 * stats (repos, open PRs, open issues, CI-active count), and per-repo CI status.
 *
 * The exported `OrgPage` owns the `/api/repos` + `/api/stats` fetches for the
 * routed desktop shell. `OrgTab` stays presentational so legacy callers and
 * focused component tests can keep passing explicit repo/stat payloads.
 */
import React, { useCallback, useEffect, useState } from "react";
import { LANG_COLORS } from "../components/formatters";
import { Stat } from "../components/Stat";
import { legacyFetch } from "../lib/api";
import { GitPrGlyph, IssueGlyph } from "./decompIcons";

function timeAgo(d?: string | null): string {
  if (!d) return "";
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

/** A single organization repository row. */
export interface OrgRepo {
  name: string;
  description?: string | null;
  language?: string | null;
  url?: string | null;
  private?: boolean;
  open_prs?: number;
  open_issues?: number;
  updated_at?: string | null;
  last_ci_status?: string | null;
  last_ci_conclusion?: string | null;
  last_ci_run_url?: string | null;
}

/** Aggregate org-level stats (only `org_open_issues` is consumed here). */
export interface OrgStats {
  org_open_issues?: number;
}

export interface OrgProps {
  /** All organization repositories (owned/polled by the legacy App). */
  repos: OrgRepo[];
  /** True while the repo list is being (re)fetched. */
  loading: boolean;
  /** Aggregate org stats; defaults to an empty object. */
  stats?: OrgStats;
}

interface ReposPayload {
  repos?: OrgRepo[];
}

type SortKey = "updated" | "prs" | "issues" | "name";

function normalizeReposPayload(payload: unknown): OrgRepo[] {
  if (Array.isArray(payload)) return payload as OrgRepo[];
  if (payload && typeof payload === "object") {
    const repos = (payload as ReposPayload).repos;
    if (Array.isArray(repos)) return repos;
  }
  return [];
}

export function OrgPage(): React.ReactElement {
  const [repos, setRepos] = useState<OrgRepo[]>([]);
  const [stats, setStats] = useState<OrgStats>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    Promise.all([
      legacyFetch("/api/repos", { signal }).then((r) => {
        if (!r.ok) throw new Error("repos HTTP " + r.status);
        return r.json();
      }),
      legacyFetch("/api/stats", { signal }).then((r) => {
        if (!r.ok) throw new Error("stats HTTP " + r.status);
        return r.json();
      }),
    ])
      .then(([reposPayload, statsPayload]) => {
        setRepos(normalizeReposPayload(reposPayload));
        setStats(
          (statsPayload && typeof statsPayload === "object"
            ? statsPayload
            : {}) as OrgStats,
        );
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load organization data",
        );
      })
      .finally(() => {
        if (!signal?.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  return (
    <div>
      {error ? (
        <div
          className="section"
          role="alert"
          style={{ marginBottom: 12, color: "var(--accent-red)" }}
        >
          Failed to load organization data: {error}
          <button
            className="btn"
            type="button"
            onClick={() => refresh()}
            style={{ marginLeft: 12 }}
          >
            Retry
          </button>
        </div>
      ) : null}
      <OrgTab repos={repos} loading={loading} stats={stats} />
    </div>
  );
}

export function OrgTab({
  repos,
  loading,
  stats,
}: OrgProps): React.ReactElement {
  const s: OrgStats = stats ?? {};
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("updated");

  const filtered = repos.filter((r) => {
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      (r.name && r.name.toLowerCase().indexOf(q) >= 0) ||
      (!!r.description && r.description.toLowerCase().indexOf(q) >= 0) ||
      (!!r.language && r.language.toLowerCase().indexOf(q) >= 0)
    );
  });
  const sorted = filtered.slice().sort((a, b) => {
    if (sortBy === "prs") return (b.open_prs || 0) - (a.open_prs || 0);
    if (sortBy === "issues") return (b.open_issues || 0) - (a.open_issues || 0);
    if (sortBy === "name") return (a.name || "").localeCompare(b.name || "");
    return (b.updated_at || "").localeCompare(a.updated_at || "");
  });

  const tPR = repos.reduce((sum, r) => sum + (r.open_prs || 0), 0);
  const tI = repos.reduce((sum, r) => sum + (r.open_issues || 0), 0);
  const wCI = repos.filter((r) => r.last_ci_status).length;

  function sortButton(key: SortKey, label: string): React.ReactElement {
    return (
      <button
        className={"btn" + (sortBy === key ? " btn-green" : "")}
        type="button"
        aria-pressed={sortBy === key}
        onClick={() => setSortBy(key)}
      >
        {label}
      </button>
    );
  }

  return (
    <div>
      <div className="stat-row">
        <Stat
          label="Repositories"
          value={repos.length}
          sub="in D-sorganization"
        />
        <Stat
          label="Open PRs"
          value={tPR}
          color={tPR > 0 ? "var(--accent-blue)" : "inherit"}
          sub="across all repos"
        />
        <Stat
          label="Open Issues"
          value={s.org_open_issues != null ? s.org_open_issues : tI}
          color={
            (s.org_open_issues || tI) > 0 ? "var(--accent-orange)" : "inherit"
          }
          sub="across all repos"
        />
        <Stat label="CI/CD Active" value={wCI} sub="repos with workflows" />
      </div>

      <div className="toolbar">
        <input
          className="search-bar"
          aria-label="Search repositories"
          placeholder="Search repos..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="toolbar-right">
          <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
            Sort:
          </span>
          {sortButton("updated", "Recent")}
          {sortButton("prs", "PRs")}
          {sortButton("issues", "Issues")}
          {sortButton("name", "Name")}
          {loading ? <span className="spinner" /> : null}
        </div>
      </div>

      <div className="section" style={{ overflowX: "auto" }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Repository</th>
              <th>Language</th>
              <th style={{ textAlign: "center" }}>PRs</th>
              <th style={{ textAlign: "center" }}>Issues</th>
              <th>CI/CD</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length > 0 ? (
              sorted.map((r) => {
                const ci = r.last_ci_conclusion || r.last_ci_status;
                return (
                  <tr key={r.name}>
                    <td>
                      <div className="repo-name-cell">
                        <div
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 8,
                          }}
                        >
                          <a
                            className="repo-name-link"
                            href={r.url ?? undefined}
                            target="_blank"
                            rel="noopener"
                          >
                            {r.name}
                          </a>
                          {r.private ? (
                            <span className="visibility-badge">private</span>
                          ) : (
                            <span
                              className="visibility-badge"
                              style={{
                                borderColor: "var(--accent-green)",
                                color: "var(--accent-green)",
                              }}
                            >
                              public
                            </span>
                          )}
                        </div>
                        {r.description ? (
                          <div className="repo-desc">{r.description}</div>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      {r.language ? (
                        <span
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                          }}
                        >
                          <span
                            className="lang-dot"
                            style={{
                              background:
                                LANG_COLORS[r.language] ||
                                "var(--text-secondary)",
                            }}
                          />
                          {r.language}
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>-</span>
                      )}
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <span
                        className={
                          "count-badge " +
                          ((r.open_prs || 0) > 0 ? "has-items" : "zero")
                        }
                      >
                        <GitPrGlyph size={14} />
                        {r.open_prs || 0}
                      </span>
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <span
                        className={
                          "count-badge " +
                          ((r.open_issues || 0) > 0 ? "has-items" : "zero")
                        }
                      >
                        <IssueGlyph size={14} />
                        {r.open_issues || 0}
                      </span>
                    </td>
                    <td>
                      {ci ? (
                        <a
                          href={r.last_ci_run_url ?? undefined}
                          target="_blank"
                          rel="noopener"
                          style={{ textDecoration: "none" }}
                        >
                          <span className={"conclusion-badge " + ci}>{ci}</span>
                        </a>
                      ) : (
                        <span
                          style={{ color: "var(--text-muted)", fontSize: 12 }}
                        >
                          No CI
                        </span>
                      )}
                    </td>
                    <td style={{ color: "var(--text-muted)" }}>
                      {timeAgo(r.updated_at)}
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td
                  colSpan={6}
                  style={{
                    textAlign: "center",
                    padding: 40,
                    color: "var(--text-muted)",
                  }}
                >
                  {loading ? "Loading..." : "No repos found"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default OrgTab;
