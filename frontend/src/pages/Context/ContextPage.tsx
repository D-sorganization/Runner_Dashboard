import React, { useState, useEffect } from "react";
import { legacyFetch } from "../../lib/api";

export interface ContextPackInfo {
  version: string;
  digest: string;
  pin: string;
  source_path: string;
  is_fallback: boolean;
}

export interface ContextLesson {
  id: string;
  repo: string;
  review_status: string;
  author_principal?: string;
  source_run?: string;
  created_at?: string;
  expires_at?: string;
  body?: { summary?: string; text?: string };
  visibility?: string;
  quarantine_reason?: string;
}

export interface ContextRunRecord {
  id: string;
  role: string;
  repo: string;
  status: string;
  created_at: string;
  standards_version?: string;
  standards_tokens?: number;
  lessons_tokens?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost_usd?: number;
  context_stamp?: string;
}

export interface ContextPageProps {
  pack?: ContextPackInfo | null;
  lessons?: ContextLesson[];
  runs?: ContextRunRecord[];
  isLoading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
}

const TOTAL_BUDGET_CAP = 2200;

export function ContextPage({
  pack: propPack,
  lessons: propLessons,
  runs: propRuns,
  error: propError = null,
  onRefresh,
}: ContextPageProps) {
  const [pack, setPack] = useState<ContextPackInfo | null>(propPack ?? null);
  const [lessons, setLessons] = useState<ContextLesson[]>(propLessons ?? []);
  const [runs, setRuns] = useState<ContextRunRecord[]>(propRuns ?? []);
  const [, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(propError);

  useEffect(() => { if (propPack !== undefined) setPack(propPack); }, [propPack]);
  useEffect(() => { if (propLessons !== undefined) setLessons(propLessons); }, [propLessons]);
  useEffect(() => { if (propRuns !== undefined) setRuns(propRuns); }, [propRuns]);
  useEffect(() => { if (propError !== undefined) setError(propError); }, [propError]);

  const fetchData = async () => {
    if (propPack !== undefined && propLessons !== undefined && propRuns !== undefined) return;
    setLoading(true);
    setError(null);
    try {
      const [sumRes, memRes, runsRes] = await Promise.all([
        legacyFetch("/api/staff/summary").catch(() => null),
        legacyFetch("/api/v1/memory?type=lesson").catch(() => null),
        legacyFetch("/api/staff/runs?limit=50").catch(() => null),
      ]);
      if (sumRes) {
        setPack({
          version: sumRes.standards_version || "unknown",
          digest: (sumRes.standards_pin || "").split("#")[1] || "unknown",
          pin: sumRes.standards_pin || "unknown",
          source_path: sumRes.standards_source || "<builtin>",
          is_fallback: Boolean(sumRes.standards_fallback),
        });
      }
      if (memRes?.entries) setLessons(memRes.entries);
      if (runsRes?.runs) setRuns(runsRes.runs);
    } catch (err: any) {
      setError(err?.message || "Failed to load context data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (propPack === undefined || propLessons === undefined || propRuns === undefined) fetchData();
  }, []);

  const publicLessons = lessons.filter((l) => l.visibility !== "barb-only");
  const counts = {
    proposed: publicLessons.filter((l) => l.review_status === "proposed").length,
    accepted: publicLessons.filter((l) => l.review_status === "accepted").length,
    quarantined: publicLessons.filter((l) => l.review_status === "quarantined").length,
    retired: publicLessons.filter((l) => l.review_status === "retired").length,
  };

  const repos = Array.from(new Set(runs.map((r) => r.repo).filter(Boolean)));
  const repoLag = repos.map((repo) => {
    const latest = runs.filter((r) => r.repo === repo)[0];
    const latestVer = latest?.standards_version || "unknown";
    return { repo, latestVer, isUpToDate: pack ? latestVer === pack.version : false, lastRunAt: latest?.created_at };
  });

  if (error) {
    return (
      <div className="p-6 max-w-7xl mx-auto space-y-4">
        <div className="bg-red-900/40 border border-red-500/50 p-4 rounded-lg flex justify-between items-center text-red-200">
          <span>{error}</span>
          <button onClick={onRefresh || fetchData} className="px-3 py-1 bg-red-800 hover:bg-red-700 rounded text-sm font-medium">Retry</button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-8 font-sans text-neutral-100">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Fleet Context & Shared Memory</h1>
        <p className="text-sm text-neutral-400 mt-1">Librarian-stewarded standards pack pin, per-repo AGENTS.md version lag, lesson review queue, and per-run context size.</p>
      </div>

      {pack && (
        <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold flex items-center gap-2">
              Current Pack Pin
              {pack.is_fallback ? (
                <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40">Builtin Fallback Warning</span>
              ) : (
                <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">Pinned & Verified</span>
              )}
            </h2>
            <span className="text-xs text-neutral-400 bg-neutral-800/80 px-2.5 py-1 rounded-md">2,200 token hard cap (Tier 0: ≤ 1,500, Tier 1 lessons: ≤ 600)</span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-sm pt-2">
            <div className="bg-neutral-950/60 p-3 rounded-lg border border-neutral-800/80"><span className="text-xs text-neutral-400 block">Pack Pin</span><span className="font-mono font-medium text-blue-400 text-xs break-all">{pack.pin}</span></div>
            <div className="bg-neutral-950/60 p-3 rounded-lg border border-neutral-800/80"><span className="text-xs text-neutral-400 block">Version</span><span className="font-mono font-medium text-neutral-200">{pack.version}</span></div>
            <div className="bg-neutral-950/60 p-3 rounded-lg border border-neutral-800/80"><span className="text-xs text-neutral-400 block">Content Digest</span><span className="font-mono text-xs text-neutral-300 break-all">{pack.digest}</span></div>
            <div className="bg-neutral-950/60 p-3 rounded-lg border border-neutral-800/80"><span className="text-xs text-neutral-400 block">Source Location</span><span className="font-mono text-xs text-neutral-300 break-all">{pack.source_path}</span></div>
          </div>
        </section>
      )}

      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-4">
        <h2 className="text-lg font-semibold">Repository AGENTS.md Version Lag</h2>
        {repoLag.length === 0 ? <p className="text-sm text-neutral-400">No active repositories detected.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead><tr className="border-b border-neutral-800 text-neutral-400 text-xs uppercase"><th className="py-2.5 px-3">Repository</th><th className="py-2.5 px-3">Last Run Pack</th><th className="py-2.5 px-3">Status</th><th className="py-2.5 px-3">Last Run Date</th></tr></thead>
              <tbody className="divide-y divide-neutral-800/50">
                {repoLag.map((r) => (
                  <tr key={r.repo} className="hover:bg-neutral-800/30">
                    <td className="py-2.5 px-3 font-medium text-neutral-200">{r.repo}</td>
                    <td className="py-2.5 px-3 font-mono text-xs text-neutral-300">{r.latestVer}</td>
                    <td className="py-2.5 px-3" data-testid={`repo-status-${r.repo}`}>
                      {r.isUpToDate ? <span className="px-2 py-0.5 text-xs rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Up to date</span> : <span className="px-2 py-0.5 text-xs rounded bg-amber-500/10 text-amber-400 border border-amber-500/30">Lagging</span>}
                    </td>
                    <td className="py-2.5 px-3 text-xs text-neutral-400">{r.lastRunAt || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-lg font-semibold">Lesson Review Queue</h2><p className="text-xs text-neutral-400">Read-only for human operators. Promoted and retired lessons cease prompt injection.</p></div>
          <div className="flex items-center gap-2 text-xs">
            <span data-testid="count-proposed" className="bg-neutral-800 px-2 py-1 rounded">Proposed: <b>{counts.proposed}</b></span>
            <span data-testid="count-accepted" className="bg-emerald-950 text-emerald-300 border border-emerald-800 px-2 py-1 rounded">Accepted: <b>{counts.accepted}</b></span>
            <span data-testid="count-quarantined" className="bg-amber-950 text-amber-300 border border-amber-800 px-2 py-1 rounded">Quarantined: <b>{counts.quarantined}</b></span>
            <span data-testid="count-retired" className="bg-neutral-800 text-neutral-400 px-2 py-1 rounded">Retired: <b>{counts.retired}</b></span>
          </div>
        </div>
        {publicLessons.length === 0 ? <p className="text-sm text-neutral-400 py-3">No lessons in review queue.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead><tr className="border-b border-neutral-800 text-neutral-400 text-xs uppercase"><th className="py-2.5 px-3">Status</th><th className="py-2.5 px-3">Lesson Summary</th><th className="py-2.5 px-3">Repo</th><th className="py-2.5 px-3">Provenance (Author / Run)</th><th className="py-2.5 px-3">Expires</th></tr></thead>
              <tbody className="divide-y divide-neutral-800/50">
                {publicLessons.map((l) => {
                  const isExpired = l.expires_at ? new Date(l.expires_at).getTime() < Date.now() : false;
                  return (
                    <tr key={l.id} className="hover:bg-neutral-800/30">
                      <td className="py-2.5 px-3">
                        <span className={`px-2 py-0.5 text-xs rounded font-medium ${l.review_status === "accepted" ? "bg-emerald-500/20 text-emerald-300" : l.review_status === "quarantined" ? "bg-amber-500/20 text-amber-300" : l.review_status === "proposed" ? "bg-blue-500/20 text-blue-300" : "bg-neutral-700 text-neutral-300"}`}>{l.review_status}</span>
                        {isExpired && <span className="ml-1 px-1.5 py-0.5 text-xs rounded bg-red-900/50 text-red-300">Expired</span>}
                      </td>
                      <td className="py-2.5 px-3">
                        <div className="text-neutral-200">{l.body?.summary || l.body?.text || "—"}</div>
                        {l.quarantine_reason && <div className="text-xs text-amber-400/90 mt-0.5">Quarantine reason: {l.quarantine_reason}</div>}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-neutral-300">{l.repo}</td>
                      <td className="py-2.5 px-3 text-xs text-neutral-400 font-mono"><div>{l.author_principal || "—"}</div><div className="text-neutral-500">{l.source_run || "—"}</div></td>
                      <td className="py-2.5 px-3 text-xs text-neutral-400">{l.expires_at?.slice(0, 10) || "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-4">
        <div><h2 className="text-lg font-semibold">Per-Run Context Size & Bloat Monitor</h2><p className="text-xs text-neutral-400">Total injected context stamped per run vs 2,200 token cap. Estimates labeled (estimate).</p></div>
        {runs.length === 0 ? <p className="text-sm text-neutral-400 py-3">No recent runs recorded.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead><tr className="border-b border-neutral-800 text-neutral-400 text-xs uppercase"><th className="py-2.5 px-3">Run ID / Role</th><th className="py-2.5 px-3">Tokens (T0 / Skills / Lessons)</th><th className="py-2.5 px-3">Total Injected</th><th className="py-2.5 px-3">Cap Status</th><th className="py-2.5 px-3">Lesson Audit (Selected / Skipped)</th><th className="py-2.5 px-3">Usage & Cost Proxies</th></tr></thead>
              <tbody className="divide-y divide-neutral-800/50">
                {runs.map((r) => {
                  let stamp: any = {};
                  try { if (r.context_stamp) stamp = JSON.parse(r.context_stamp); } catch {}
                  const t0 = stamp?.token_estimates?.tier0_standards ?? (r.standards_tokens || 0);
                  const t1Skills = stamp?.token_estimates?.tier1_skills ?? 0;
                  const t1Lessons = stamp?.token_estimates?.tier1_lessons ?? (r.lessons_tokens || 0);
                  const total = stamp?.token_estimates?.total_injected ?? (t0 + t1Skills + t1Lessons);
                  const isOverCap = total > TOTAL_BUDGET_CAP;
                  const selectedIds: string[] = stamp?.lesson_ids || [];
                  const skippedItems: Array<{ id: string; reason: string }> = stamp?.lessons_skipped || [];

                  return (
                    <tr key={r.id} className="hover:bg-neutral-800/30">
                      <td className="py-2.5 px-3"><div className="font-mono text-xs font-semibold text-neutral-200">{r.id}</div><div className="text-xs text-neutral-400">{r.role} ({r.repo})</div></td>
                      <td className="py-2.5 px-3 font-mono text-xs text-neutral-300">{t0} / {t1Skills} / {t1Lessons}</td>
                      <td className="py-2.5 px-3 font-mono text-xs text-neutral-200 font-medium">{total} tokens</td>
                      <td className="py-2.5 px-3" data-testid={`run-cap-${r.id}`}>
                        {isOverCap ? <span className="px-2 py-0.5 text-xs rounded bg-red-900/40 text-red-300 border border-red-700/60 font-medium">Over-Cap</span> : <span className="px-2 py-0.5 text-xs rounded bg-emerald-950 text-emerald-400 border border-emerald-800">OK</span>}
                      </td>
                      <td className="py-2.5 px-3 text-xs">
                        <div className="text-neutral-300">{selectedIds.length > 0 ? selectedIds.join(", ") : <span className="text-neutral-500">None</span>}</div>
                        {skippedItems.length > 0 && <div className="text-neutral-500 text-xs mt-0.5">Skipped: {skippedItems.map((s) => `${s.id} (${s.reason})`).join(", ")}</div>}
                      </td>
                      <td className="py-2.5 px-3 text-xs text-neutral-400 font-mono">
                        <div>In: {r.input_tokens || 0} / Out: {r.output_tokens || 0}</div>
                        <div>Cost: ~${(r.cost_usd || 0).toFixed(3)} <span className="text-neutral-500">(estimate)</span></div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
export default ContextPage;
