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

export interface ContextSkillInfo {
  name: string;
  version: string;
  pin: string;
  content_hash: string;
  source_path: string;
}

export interface ContextRunRecord {
  skill_pin?: string;
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
  skills?: ContextSkillInfo[] | null;
  lessons?: ContextLesson[];
  runs?: ContextRunRecord[];
  isLoading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
}

const TOTAL_BUDGET_CAP = 2200;
type RequestState = "loading" | "available" | "unavailable";

function requestState<T>(value: T | undefined): RequestState {
  return value === undefined ? "loading" : value === null ? "unavailable" : "available";
}

export function ContextPage({
  pack: propPack,
  skills: propSkills,
  lessons: propLessons,
  runs: propRuns,
  error: propError = null,
  onRefresh,
}: ContextPageProps) {
  const [pack, setPack] = useState<ContextPackInfo | null>(propPack ?? null);
  const [skills, setSkills] = useState<ContextSkillInfo[]>(propSkills ?? []);
  const [skillsState, setSkillsState] = useState<RequestState>(propSkills === undefined ? "loading" : propSkills === null ? "unavailable" : "available");
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [lessons, setLessons] = useState<ContextLesson[]>(propLessons ?? []);
  const [runs, setRuns] = useState<ContextRunRecord[]>(propRuns ?? []);
  const [packState, setPackState] = useState<RequestState>(requestState(propPack));
  const [lessonsState, setLessonsState] = useState<RequestState>(requestState(propLessons));
  const [runsState, setRunsState] = useState<RequestState>(requestState(propRuns));
  const [lessonsError, setLessonsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(propError);

  useEffect(() => { if (propPack !== undefined) { setPack(propPack); setPackState(requestState(propPack)); } }, [propPack]);
  useEffect(() => { if (propSkills !== undefined) { setSkills(propSkills ?? []); setSkillsState(propSkills === null ? "unavailable" : "available"); } }, [propSkills]);
  useEffect(() => { if (propLessons !== undefined) { setLessons(propLessons); setLessonsState(requestState(propLessons)); } }, [propLessons]);
  useEffect(() => { if (propRuns !== undefined) { setRuns(propRuns); setRunsState(requestState(propRuns)); } }, [propRuns]);
  useEffect(() => { if (propError !== undefined) setError(propError); }, [propError]);

  const fetchSkills = async () => {
    setSkillsState("loading");
    setSkillsError(null);
    try {
      const response = await legacyFetch("/api/v1/staff/skills");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result: unknown = await response.json();
      if (typeof result !== "object" || result === null || !("skills" in result) || !Array.isArray(result.skills)) {
        throw new Error("The service returned an invalid skill registry response.");
      }
      setSkills(result.skills as ContextSkillInfo[]);
      setSkillsState("available");
    } catch (err: unknown) {
      setSkillsState("unavailable");
      setSkillsError(err instanceof Error ? err.message : "Could not load the skill registry.");
    }
  };

  const fetchData = async () => {
    if (propPack !== undefined && propLessons !== undefined && propRuns !== undefined) return;
    setError(null);
    setLessonsError(null);
    setPackState("loading");
    setLessonsState("loading");
    setRunsState("loading");
    const loadJson = async (url: string): Promise<unknown> => {
      const response = await legacyFetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    };
    const [summaryResult, lessonsResult, runsResult] = await Promise.allSettled([
      loadJson("/api/staff/summary"),
      loadJson("/api/v1/memory?type=lesson"),
      loadJson("/api/staff/runs?limit=50"),
    ]);
    if (summaryResult.status === "fulfilled" && typeof summaryResult.value === "object" && summaryResult.value !== null && "standards_version" in summaryResult.value) {
      const summary = summaryResult.value as Record<string, unknown>;
      const pin = typeof summary.standards_pin === "string" ? summary.standards_pin : "unknown";
      setPack({
        version: typeof summary.standards_version === "string" ? summary.standards_version : "unknown",
        digest: pin.split("#")[1] || "unknown",
        pin,
        source_path: typeof summary.standards_source === "string" ? summary.standards_source : "<builtin>",
        is_fallback: Boolean(summary.standards_fallback),
      });
      setPackState("available");
    } else {
      setPackState("unavailable");
    }
    if (lessonsResult.status === "fulfilled" && typeof lessonsResult.value === "object" && lessonsResult.value !== null && "entries" in lessonsResult.value && Array.isArray(lessonsResult.value.entries)) {
      setLessons(lessonsResult.value.entries as ContextLesson[]);
      setLessonsState("available");
    } else {
      setLessonsState("unavailable");
      setLessonsError(lessonsResult.status === "rejected" ? String(lessonsResult.reason) : "The service returned an invalid lesson response.");
    }
    if (runsResult.status === "fulfilled" && typeof runsResult.value === "object" && runsResult.value !== null && "runs" in runsResult.value && Array.isArray(runsResult.value.runs)) {
      setRuns(runsResult.value.runs as ContextRunRecord[]);
      setRunsState("available");
    } else {
      setRunsState("unavailable");
    }
  };

  useEffect(() => {
    if (propSkills === undefined) void fetchSkills();
  }, [propSkills]);

  useEffect(() => {
    if (propPack === undefined || propLessons === undefined || propRuns === undefined) {
      void fetchData();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    return { repo, latestVer, isUpToDate: pack ? latestVer === pack.version : false, canCompare: Boolean(pack), lastRunAt: latest?.created_at };
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
        <p className="text-sm text-neutral-400 mt-1">Tier-0 standards pin, current Tier-1 skill registry, latest per-repo run versions, lesson review queue, and per-run context estimates. AGENTS.md synchronization is not reported by the current API.</p>
      </div>

      {pack ? (
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
          <p className="text-xs text-neutral-400">Tier-1 skills are reported separately in the registry below.</p>
        </section>
      ) : (
        <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 text-sm text-neutral-300" role="status">
          {packState === "loading" ? "Loading Tier-0 pack data…" : "Tier-0 pack data is unavailable."}
          {packState === "unavailable" && <button onClick={onRefresh || fetchData} className="ml-3 rounded bg-neutral-800 px-3 py-1 text-xs">Retry pack data</button>}
        </section>
      )}

      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-4">
        <div><h2 className="text-lg font-semibold">Current Tier-1 Skill Packs</h2><p className="text-xs text-neutral-400">Registry versions and content pins from the staff skills API.</p></div>
        {skillsState === "loading" ? <p className="text-sm text-neutral-400">Loading skill registry &</p> : skillsState === "unavailable" ? <div className="text-sm text-amber-300" role="status">Tier-1 skill registry unavailable{skillsError ? ` (${skillsError})` : ""}.<button onClick={fetchSkills} className="ml-3 rounded bg-neutral-800 px-3 py-1 text-xs">Retry skills</button></div> : skills.length === 0 ? <p className="text-sm text-neutral-400">No Tier-1 skills are currently registered.</p> : (
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {skills.map((skill) => <li key={skill.pin} className="min-w-0 rounded-lg border border-neutral-800 bg-neutral-950/60 p-3">
              <div className="font-medium text-neutral-200">{skill.name} <span className="font-mono text-xs text-neutral-400">v{skill.version}</span></div>
              <div className="mt-1 break-all font-mono text-xs text-blue-300">{skill.pin}</div>
              <div className="mt-1 break-all font-mono text-xs text-neutral-500">{skill.source_path}</div>
            </li>)}
          </ul>
        )}
      </section>

      <section className="bg-neutral-900 border border-neutral-800 rounded-xl p-5 shadow-sm space-y-4">
        <h2 className="text-lg font-semibold">Standards Pack Version at Latest Recorded Run</h2>
        <p className="text-xs text-neutral-400">AGENTS.md sync versions are not exposed by the current API; this table compares run records with the current Tier-0 pack only.</p>
        {runsState === "loading" ? <p className="text-sm text-neutral-400">Loading run version data…</p> : runsState === "unavailable" ? <div className="text-sm text-amber-300" role="status">Run version data is unavailable.<button onClick={onRefresh || fetchData} className="ml-3 rounded bg-neutral-800 px-3 py-1 text-xs">Retry run data</button></div> : repoLag.length === 0 ? <p className="text-sm text-neutral-400">No active repositories detected.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead><tr className="border-b border-neutral-800 text-neutral-400 text-xs uppercase"><th className="py-2.5 px-3">Repository</th><th className="py-2.5 px-3">Last Run Pack</th><th className="py-2.5 px-3">Status</th><th className="py-2.5 px-3">Last Run Date</th></tr></thead>
              <tbody className="divide-y divide-neutral-800/50">
                {repoLag.map((r) => (
                  <tr key={r.repo} className="hover:bg-neutral-800/30">
                    <td className="py-2.5 px-3 font-medium text-neutral-200">{r.repo}</td>
                    <td className="py-2.5 px-3 font-mono text-xs text-neutral-300">{r.latestVer}</td>
                    <td className="py-2.5 px-3" data-testid={`repo-status-${r.repo}`}>
                      {!r.canCompare ? <span className="px-2 py-0.5 text-xs rounded bg-neutral-800 text-neutral-400">Cannot compare</span> : r.isUpToDate ? <span className="px-2 py-0.5 text-xs rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">Matches current Tier-0</span> : <span className="px-2 py-0.5 text-xs rounded bg-amber-500/10 text-amber-400 border border-amber-500/30">Different Tier-0 version</span>}
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
          {lessonsState === "available" && <div className="flex items-center gap-2 text-xs">
            <span data-testid="count-proposed" className="bg-neutral-800 px-2 py-1 rounded">Proposed: <b>{counts.proposed}</b></span>
            <span data-testid="count-accepted" className="bg-emerald-950 text-emerald-300 border border-emerald-800 px-2 py-1 rounded">Accepted: <b>{counts.accepted}</b></span>
            <span data-testid="count-quarantined" className="bg-amber-950 text-amber-300 border border-amber-800 px-2 py-1 rounded">Quarantined: <b>{counts.quarantined}</b></span>
            <span data-testid="count-retired" className="bg-neutral-800 text-neutral-400 px-2 py-1 rounded">Retired: <b>{counts.retired}</b></span>
          </div>}
        </div>
        {lessonsState === "loading" ? <p className="text-sm text-neutral-400 py-3">Loading lesson review data…</p> : lessonsState === "unavailable" ? <div className="text-sm text-amber-300 py-3" role="status">Lesson review data unavailable{lessonsError ? ` (${lessonsError})` : ""}.<button onClick={onRefresh || fetchData} className="ml-3 rounded bg-neutral-800 px-3 py-1 text-xs" aria-label="Retry lesson data">Retry</button></div> : publicLessons.length === 0 ? <p className="text-sm text-neutral-400 py-3">No lessons in review queue.</p> : (
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
        <div><h2 className="text-lg font-semibold">Per-Run Context Size & Bloat Monitor</h2><p className="text-xs text-neutral-400">Known Tier-0/Tier-1 component subtotal; library tokens are not yet recorded. Full-prompt estimates are separate and are not compared with the context cap.</p></div>
        {runsState === "loading" ? <p className="text-sm text-neutral-400 py-3">Loading run context data…</p> : runsState === "unavailable" ? <div className="text-sm text-amber-300" role="status">Run context data is unavailable.<button onClick={onRefresh || fetchData} className="ml-3 rounded bg-neutral-800 px-3 py-1 text-xs">Retry run data</button></div> : runs.length === 0 ? <p className="text-sm text-neutral-400 py-3">No recent runs recorded.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm border-collapse">
              <thead><tr className="border-b border-neutral-800 text-neutral-400 text-xs uppercase"><th className="py-2.5 px-3">Run ID / Role</th><th className="py-2.5 px-3">Tokens (T0 / Skills / Lessons)</th><th className="py-2.5 px-3">Known Context Subtotal</th><th className="py-2.5 px-3">Cap Status</th><th className="py-2.5 px-3">Lesson Audit (Selected / Skipped)</th><th className="py-2.5 px-3">Usage & Cost Proxies</th></tr></thead>
              <tbody className="divide-y divide-neutral-800/50">
                {runs.map((r) => {
                  let stamp: Record<string, unknown> | null = null;
                  try {
                    if (r.context_stamp) {
                      stamp = JSON.parse(r.context_stamp) as Record<string, unknown>;
                    }
                  } catch (e: unknown) {
                    void e;
                  }
                  const tokenEstimates = stamp?.token_estimates as Record<string, number> | undefined;
                  const t0 = tokenEstimates?.tier0_standards ?? (r.standards_tokens || 0);
                  const t1Skills = tokenEstimates?.tier1_skills ?? 0;
                  const t1Lessons = tokenEstimates?.tier1_lessons ?? (r.lessons_tokens || 0);
                  const total = t0 + t1Skills + t1Lessons;
                  const fullPromptEstimate = tokenEstimates?.full_prompt ?? (stamp?.context_accounting_version === 2 ? undefined : tokenEstimates?.total_injected);
                  const isOverCap = total > TOTAL_BUDGET_CAP;
                  const selectedIds: string[] = Array.isArray(stamp?.lesson_ids) ? (stamp.lesson_ids as string[]) : [];
                  const skippedItems: Array<{ id: string; reason: string }> = Array.isArray(stamp?.lessons_skipped)
                    ? (stamp.lessons_skipped as Array<{ id: string; reason: string }>)
                    : [];

                  return (
                    <tr key={r.id} className="hover:bg-neutral-800/30">
                      <td className="py-2.5 px-3"><div className="font-mono text-xs font-semibold text-neutral-200">{r.id}</div><div className="text-xs text-neutral-400">{r.role} ({r.repo})</div>{r.skill_pin && <div className="mt-1 break-all font-mono text-xs text-neutral-500">Tier-1 skill: {r.skill_pin}</div>}</td>
                      <td className="py-2.5 px-3 font-mono text-xs text-neutral-300">{t0} / {t1Skills} / {t1Lessons}</td>
                      <td className="py-2.5 px-3 font-mono text-xs text-neutral-200 font-medium"><div>{total} known tokens</div>{fullPromptEstimate !== undefined && <div className="mt-1 text-neutral-500">Full prompt: {fullPromptEstimate} (estimate)</div>}</td>
                      <td className="py-2.5 px-3" data-testid={`run-cap-${r.id}`}>
                        {isOverCap ? <span className="px-2 py-0.5 text-xs rounded bg-red-900/40 text-red-300 border border-red-700/60 font-medium">Over-Cap</span> : <span className="px-2 py-0.5 text-xs rounded bg-amber-950 text-amber-300 border border-amber-800">Partial</span>}
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
