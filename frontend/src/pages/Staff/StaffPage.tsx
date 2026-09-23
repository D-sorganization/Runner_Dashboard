/**
 * StaffPage.tsx — the "Staff" tab (issue #1198, epic #1192).
 *
 * Composition: the Board status monitor on top (polled), then a SubTabs strip
 * with Roster, Runs, Assign and Holds. The roster is fetched once here and
 * shared with Roster, Assign (role/provider options), RunLog and Holds
 * (role filter / applies-to) — one source of truth per page (DRY).
 *
 * Selecting a run (from the Runs table or the Board) swaps the Runs section
 * for RunDetail; dispatching from Assign jumps straight to the new run, and a
 * `?run=<id>` query opens that run directly (deep link from Fleet Command).
 */
import { useCallback, useEffect, useState } from "react";
import { SubTabs } from "../../components/SubTabs";
import { Assign } from "./Assign";
import { Board } from "./Board";
import { Holds } from "./Holds";
import { Roster } from "./Roster";
import { RunDetail } from "./RunDetail";
import { RunLog } from "./RunLog";
import { errorMessage, fetchRoster, type RosterResponse } from "./staffApi";

export type StaffSection = "roster" | "runs" | "assign" | "holds";

const SECTION_TABS: { key: StaffSection; label: string }[] = [
  { key: "roster", label: "Roster" },
  { key: "runs", label: "Runs" },
  { key: "assign", label: "Assign" },
  { key: "holds", label: "Holds" },
];

/** Run id deep-linked via `?run=<id>` (Fleet Command's "Open run", #1233). */
function runFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("run") || null;
}

export function StaffPage() {
  const [roster, setRoster] = useState<RosterResponse | null>(null);
  const [rosterLoading, setRosterLoading] = useState(true);
  const [rosterError, setRosterError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(runFromUrl);
  const [section, setSection] = useState<StaffSection>(() => (selectedRun ? "runs" : "roster"));
  const [assignRole, setAssignRole] = useState<string | undefined>(undefined);
  const [runsRefresh, setRunsRefresh] = useState(0);

  const loadRoster = useCallback((signal?: AbortSignal) => {
    setRosterLoading(true);
    setRosterError(null);
    fetchRoster(signal)
      .then((data) => {
        setRoster(data);
        setRosterLoading(false);
      })
      .catch((e: unknown) => {
        if (signal?.aborted) return;
        setRosterError(errorMessage(e));
        setRosterLoading(false);
      });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    loadRoster(controller.signal);
    return () => controller.abort();
  }, [loadRoster]);

  const openRun = useCallback((id: string) => {
    setSelectedRun(id);
    setSection("runs");
  }, []);

  const onDispatched = useCallback(
    (id: string) => {
      setRunsRefresh((n) => n + 1);
      openRun(id);
    },
    [openRun],
  );

  const onAssign = useCallback((role: string) => {
    setAssignRole(role);
    setSection("assign");
  }, []);

  const roleNames = roster ? roster.roles.map((r) => r.name) : [];

  return (
    <div className="staff">
      <Board onOpenRun={openRun} />
      <SubTabs
        tabs={SECTION_TABS}
        activeKey={section}
        onChange={(key) => setSection(key as StaffSection)}
        ariaLabel="Staff sections"
        className="staff__tabs"
      />
      {section === "roster" ? (
        <Roster
          roster={roster}
          loading={rosterLoading}
          error={rosterError}
          onRetry={() => loadRoster()}
          onAssign={onAssign}
        />
      ) : null}
      {section === "runs" && selectedRun ? (
        <RunDetail runId={selectedRun} onBack={() => setSelectedRun(null)} />
      ) : null}
      {section === "runs" && !selectedRun ? (
        <RunLog roles={roleNames} onOpenRun={openRun} refreshKey={runsRefresh} />
      ) : null}
      {section === "assign" ? <Assign roster={roster} initialRole={assignRole} onDispatched={onDispatched} /> : null}
      {section === "holds" ? <Holds roles={roleNames} /> : null}
    </div>
  );
}

export default StaffPage;
