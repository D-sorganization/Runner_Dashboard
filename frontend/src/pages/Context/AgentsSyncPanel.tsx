import React, { useCallback, useEffect, useState } from "react";
import { legacyFetch } from "../../lib/api";

export interface AgentsSyncRepository {
  repo: string;
  status: "current" | "lagging" | "unavailable";
  changed_blocks: string[];
  expected_digest: string;
  observed_digest: string | null;
  expected_blocks: Record<string, string>;
  observed_blocks: Record<string, string> | null;
  last_seen_at: string | null;
}

export interface AgentsSyncReport {
  source: string;
  source_ref: string;
  status: "current" | "lagging" | "unavailable";
  reason: string | null;
  checked_at: string;
  source_digest: string;
  source_blocks: Record<string, string>;
  repositories: AgentsSyncRepository[];
}

export type LoadAgentsSync = () => Promise<AgentsSyncReport>;

const loadAgentsSync: LoadAgentsSync = async () => {
  const response = await legacyFetch("/api/v1/staff/context/agents-sync");
  if (!response.ok) throw new Error("Repository metadata is unavailable (HTTP " + response.status + ").");
  return response.json() as Promise<AgentsSyncReport>;
};

const statusClasses: Record<AgentsSyncRepository["status"], string> = {
  current: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30",
  lagging: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  unavailable: "bg-neutral-800 text-neutral-400 border-neutral-700",
};

export function AgentsSyncPanel({ load = loadAgentsSync }: { load?: LoadAgentsSync }) {
  const [report, setReport] = useState<AgentsSyncReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await load());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Repository metadata is unavailable.");
    } finally {
      setLoading(false);
    }
  }, [load]);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-4" aria-labelledby="agents-sync-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h2 id="agents-sync-title" className="text-lg font-semibold">Repository AGENTS.md Sync</h2><p className="text-xs text-neutral-400">Compared by managed-block content with {report?.source || "Repository_Management/AGENTS.md"} ({report?.source_ref || "GitHub Contents API default branch"}).</p>
          {!loading && report && report.source_digest && <p className="mt-1 break-all font-mono text-[11px] text-neutral-500">Expected SHA-256 {report.source_digest} · checked {report.checked_at}</p>}</div>
        <button type="button" onClick={() => void refresh()} className="px-3 py-1.5 rounded-md border border-neutral-700 text-xs text-neutral-300 hover:bg-neutral-800 disabled:opacity-50" disabled={loading}>Refresh</button>
      </div>
      {loading && <p role="status" className="text-sm text-neutral-400">Checking managed AGENTS.md blocks…</p>}
      {!loading && error && <div className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-3 text-sm text-amber-200"><p>{error}</p><button type="button" onClick={() => void refresh()} className="mt-2 underline">Retry</button></div>}
      {!loading && !error && report?.reason && <p role="status" className="text-sm text-amber-200">{report.reason}</p>}
      {!loading && !error && report && report.repositories.length === 0 && !report.reason && <p className="text-sm text-neutral-400">No repositories are configured for sync comparison.</p>}
      {!loading && !error && report && report.repositories.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm border-collapse">
            <thead><tr className="border-b border-neutral-800 text-neutral-400 text-xs uppercase"><th className="py-2.5 px-3">Repository</th><th className="py-2.5 px-3">Managed-block status</th><th className="py-2.5 px-3">Observed SHA-256</th><th className="py-2.5 px-3">Differences / last seen</th></tr></thead>
            <tbody className="divide-y divide-neutral-800/50">{report.repositories.map((repo) => (
              <tr key={repo.repo}>
                <td className="py-2.5 px-3 font-medium text-neutral-200">{repo.repo}</td>
                <td className="py-2.5 px-3"><span data-testid={"agents-status-" + repo.repo} className={"px-2 py-0.5 text-xs rounded border " + statusClasses[repo.status]}>{repo.status === "current" ? "In sync" : repo.status === "lagging" ? "Drift detected" : "Unavailable"}</span></td>
                <td className="py-2.5 px-3 max-w-64 break-all font-mono text-[11px] text-neutral-500">{repo.observed_digest || "Unavailable"}</td>
                <td className="py-2.5 px-3 text-xs text-neutral-400"><div>{repo.status === "unavailable" ? "Source could not be read" : repo.changed_blocks.length ? repo.changed_blocks.join(", ") : "—"}</div><div className="mt-1 text-[11px] text-neutral-500">Last seen: {repo.last_seen_at || "unavailable"}</div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}
