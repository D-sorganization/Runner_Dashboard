/**
 * Scan coverage for the Security page (epic #2566, issue #2574).
 *
 * One row per tracked repository from `GET /api/v1/security/coverage`: whether
 * its contract is enrolled and valid, whether the owner enabled scanning, the
 * last scan and whether it is due. The backend sorts due repositories first.
 */
import React from "react";
import { Badge } from "../../primitives/Badge";
import { EmptyState } from "../../primitives/EmptyState";
import type { CoverageOverview, CoverageRow } from "./securityApi";

type Tone = "success" | "warning" | "danger" | "neutral";

/** The one status that explains why a repository is or is not scanned. */
function coverageStatus(row: CoverageRow): { label: string; tone: Tone } {
  if (!row.contract_present) return { label: "not enrolled", tone: "neutral" };
  if (!row.contract_valid) return { label: "invalid contract", tone: "danger" };
  if (row.contract_disabled) return { label: "contract disabled", tone: "neutral" };
  if (!row.enabled) return { label: "not enabled", tone: "neutral" };
  if (row.due) return { label: "due", tone: "warning" };
  return { label: "current", tone: "success" };
}

function ScheduleNote({ schedule }: { schedule: CoverageOverview["schedule"] }): React.ReactElement {
  return (
    <p className="staff-muted" data-testid="security-coverage-schedule" style={{ margin: "0 0 8px" }}>
      {schedule.enabled
        ? `The scheduler is on: it checks every ${schedule.check_minutes} min in the scan window, and a failed or aborted scan retries after ${schedule.retry_hours} h.`
        : "The scheduler is off. Real scans run only after the owner enables it in config/security_scan.json."}
    </p>
  );
}

export function CoverageSection({ data }: { data: CoverageOverview }): React.ReactElement {
  const { summary } = data;
  return (
    <>
      <p data-testid="security-coverage-summary" style={{ margin: "0 0 4px" }}>
        {`${summary.with_contract} of ${summary.repos} enrolled · ${summary.scanned} scanned · ${summary.due} due`}
      </p>
      <ScheduleNote schedule={data.schedule} />
      {data.coverage.length === 0 ? (
        <EmptyState title="No tracked repositories" description="No repository is configured for this dashboard." />
      ) : (
        <table className="staff-table" data-testid="security-coverage-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              <th scope="col">Status</th>
              <th scope="col">Priority</th>
              <th scope="col">Full scan every</th>
              <th scope="col">Last scan</th>
              <th scope="col">Last success</th>
            </tr>
          </thead>
          <tbody>
            {data.coverage.map((row) => {
              const status = coverageStatus(row);
              return (
                <tr key={row.repo}>
                  <th scope="row">{row.repo}</th>
                  <td>
                    <Badge tone={status.tone} size="sm">{status.label}</Badge>
                  </td>
                  <td>{row.priority}</td>
                  <td>{`${row.full_scan_days} d`}</td>
                  <td>
                    {row.last_scan_at ? (
                      <>
                        {row.last_scan_at} <span>{row.last_state}</span>
                      </>
                    ) : (
                      "never"
                    )}
                  </td>
                  <td>{row.last_success_at || "never"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
