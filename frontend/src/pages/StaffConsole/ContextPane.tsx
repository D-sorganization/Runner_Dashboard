/**
 * ContextPane.tsx — Context inspection sidebar for selected Staff role / thread.
 *
 * Implements Workstream C (Issue #1721, Epic #1718).
 */
import React, { useState, useEffect } from "react";
import type { ContextPaneProps } from "./contextTypes";
import { RoleReadinessSection, RunList, WorkItemList } from "./ContextReadiness";
import { Skeleton, SkeletonLine } from "../../primitives/Skeleton";
import "./context.css";

export const ContextPane: React.FC<ContextPaneProps> = ({
  role,
  threadContext,
  onToggleSchedule,
  scheduleDisabledReason,
  className = "",
  isLoading = false,
}) => {
  const [activeTab, setActiveTab] = useState<"role" | "thread">("role");
  // Unknown (null) until the schedule source answers; never defaulted to enabled (#1804).
  const [scheduleEnabled, setScheduleEnabled] = useState<boolean | null>(role?.schedule?.enabled ?? null);
  const [isToggling, setIsToggling] = useState<boolean>(false);
  const [toggleError, setToggleError] = useState<string | null>(null);

  useEffect(() => {
    setScheduleEnabled(role?.schedule?.enabled ?? null);
  }, [role?.schedule?.enabled]);

  // An enabled switch always persists; otherwise it is disabled and says why (#1804).
  const switchDisabledReason = !onToggleSchedule
    ? "Read-only: schedule changes are not available here."
    : scheduleDisabledReason || (scheduleEnabled === null ? "Schedule status unknown." : null);

  const handleToggle = async () => {
    if (!role || !onToggleSchedule || isToggling || switchDisabledReason || scheduleEnabled === null) return;
    const targetState = !scheduleEnabled;
    const previousState = scheduleEnabled;

    // Optimistic update
    setScheduleEnabled(targetState);
    setIsToggling(true);
    setToggleError(null);

    try {
      await onToggleSchedule(role.name, targetState);
    } catch (err: unknown) {
      // Revert on error
      setScheduleEnabled(previousState);
      const msg = err instanceof Error ? err.message : "Failed to toggle schedule";
      setToggleError(msg);
    } finally {
      setIsToggling(false);
    }
  };

  const spendToday = role?.budget?.usd_today;
  const spendKnown = typeof spendToday === "number";
  const dailyCap = role?.budget?.usd_per_day ?? 0;
  const budgetPct = spendKnown && dailyCap > 0 ? Math.min(100, Math.round((spendToday / dailyCap) * 100)) : 0;
  const budgetDanger = spendKnown && dailyCap > 0 && spendToday >= dailyCap;
  const budgetWarning = spendKnown && dailyCap > 0 && spendToday >= dailyCap * 0.8 && !budgetDanger;

  return (
    <aside
      className={`staff-context-pane ${className}`.trim()}
      aria-label="Context pane"
    >
      {/* Tabbed Header (Role / Thread) */}
      <div
        role="tablist"
        aria-label="Context views"
        className="context-tablist"
      >
        <button
          role="tab"
          type="button"
          aria-selected={activeTab === "role"}
          onClick={() => setActiveTab("role")}
          className={`context-tab ${activeTab === "role" ? "context-tab--active" : ""}`}
        >
          Role
        </button>
        <button
          role="tab"
          type="button"
          aria-selected={activeTab === "thread"}
          onClick={() => setActiveTab("thread")}
          className={`context-tab ${activeTab === "thread" ? "context-tab--active" : ""}`}
        >
          Thread
        </button>
      </div>

      {/* Content Area */}
      <div className="context-content">
        {toggleError && (
          <div
            role="alert"
            className="context-error-alert"
          >
            {toggleError}
          </div>
        )}

        {/* Loading Skeletons */}
        {isLoading && (
          <div className="context-skeleton-wrap" aria-label="Loading context details" role="status">
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <SkeletonLine height={16} width="55%" />
              <SkeletonLine height={12} width="30%" />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <SkeletonLine height={10} width="25%" />
              <SkeletonLine height={12} width="100%" />
              <SkeletonLine height={12} width="90%" />
              <SkeletonLine height={12} width="70%" />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <SkeletonLine height={10} width="30%" />
              <div style={{ display: "flex", gap: "6px" }}>
                <Skeleton height={22} width={65} radius={9999} />
                <Skeleton height={22} width={75} radius={9999} />
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              <SkeletonLine height={10} width="35%" />
              <Skeleton height={4} width="100%" radius={9999} />
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <SkeletonLine height={11} width="28%" />
                <SkeletonLine height={11} width="28%" />
              </div>
            </div>
          </div>
        )}

        {/* ROLE TAB */}
        {!isLoading && activeTab === "role" && role && (
          <div
            role="tabpanel"
            aria-label="Role details"
            className="context-tabpanel"
          >
            {/* Header */}
            <div className="context-role-header">
              <h3 className="context-role-title">
                {role.title}
              </h3>
              <div className="context-role-handle">
                @{role.name}
              </div>
            </div>

            <RoleReadinessSection role={role} />

            {/* Mandate in prose style */}
            <div>
              <div className="context-section-label">
                Mandate
              </div>
              <p className="context-mandate-prose">
                {role.mandate}
              </p>
            </div>

            {/* Schedule Section */}
            {role.schedule && (
              <div className="context-schedule-card">
                <div className="context-schedule-header">
                  <span className="context-section-label" style={{ marginBottom: 0 }}>
                    Schedule
                  </span>
                  {/* Accessible Toggle Switch */}
                  <button
                    type="button"
                    role="switch"
                    aria-checked={scheduleEnabled === true}
                    aria-label="Toggle schedule"
                    onClick={handleToggle}
                    disabled={isToggling || Boolean(switchDisabledReason)}
                    title={switchDisabledReason ?? undefined}
                    className={`context-switch-btn ${
                      scheduleEnabled ? "context-switch-btn--checked" : ""
                    }`}
                  >
                    <span className="context-switch-knob" />
                  </button>
                </div>
                <div className="context-schedule-cron">{role.schedule.cron}</div>
                {role.schedule.window && (
                  <div className="context-schedule-window">
                    Window: {role.schedule.window}
                  </div>
                )}
                <div
                  className={`context-schedule-status ${
                    scheduleEnabled
                      ? "context-schedule-status--active"
                      : "context-schedule-status--paused"
                  }`}
                >
                  {scheduleEnabled === null ? "Schedule status unknown" : scheduleEnabled ? "Active" : "Schedule Paused"}
                </div>
                {role.schedule.hold && (
                  <div className="context-schedule-window" data-testid="context-role-hold">
                    Held: {role.schedule.hold}
                  </div>
                )}
                {switchDisabledReason && (
                  <div className="context-schedule-window" data-testid="context-schedule-reason">
                    {switchDisabledReason}
                  </div>
                )}
              </div>
            )}

            {/* Budget & Spend: Thin progress bar + numbers */}
            {role.budget && (
              <div className="context-budget-block">
                <div className="context-section-label">
                  Budget & Spend
                </div>
                <div className="context-budget-bar-track">
                  <div
                    className={`context-budget-bar-fill ${
                      budgetDanger
                        ? "context-budget-bar-fill--danger"
                        : budgetWarning
                        ? "context-budget-bar-fill--warning"
                        : ""
                    }`}
                    style={{ width: `${budgetPct}%` }}
                  />
                </div>
                <div className="context-budget-numbers">
                  <span className="context-budget-label">
                    Today:{" "}
                    <span className="context-budget-val">
                      {spendKnown ? `$${spendToday.toFixed(2)}` : "—"}
                    </span>
                  </span>
                  <span className="context-budget-label">
                    Daily Cap: <span className="context-budget-val">${role.budget.usd_per_day.toFixed(2)}</span>
                  </span>
                </div>
              </div>
            )}

            {/* Active Runs and work items owned by the role (#1804) */}
            <div>
              <div className="context-section-label">Active Runs</div>
              <RunList runs={role.active_runs} state={role.sources?.runs} />
            </div>
            <div>
              <div className="context-section-label">Work Items</div>
              <WorkItemList items={role.recent_work_items} state={role.sources?.work_items} />
            </div>
          </div>
        )}

        {/* Empty state when no role is selected */}
        {!isLoading && activeTab === "role" && !role && (
          <div className="context-empty-text">
            No role selected. Choose a staff role from the roster to view details.
          </div>
        )}

        {/* THREAD TAB */}
        {!isLoading && activeTab === "thread" && (
          <div
            role="tabpanel"
            aria-label="Thread linked items"
            className="context-tabpanel"
          >
            {threadContext?.thread_id && (
              <div>
                <div className="context-section-label">
                  Export Thread
                </div>
                <div className="context-export-links">
                  <a
                    href={`/api/v1/staff/threads/${threadContext.thread_id}/export?format=markdown`}
                    download={`thread-${threadContext.thread_id}.md`}
                    className="context-export-link"
                  >
                    Export Markdown
                  </a>
                  <a
                    href={`/api/v1/staff/threads/${threadContext.thread_id}/export?format=json`}
                    download={`thread-${threadContext.thread_id}.json`}
                    className="context-export-link"
                  >
                    Export JSON
                  </a>
                </div>
              </div>
            )}

            <div>
              <div className="context-section-label">
                Linked Work Items
              </div>
              <WorkItemList items={threadContext?.linked_work_items} state={threadContext?.sources?.work_items} />
            </div>

            <div>
              <div className="context-section-label">Linked Runs</div>
              <RunList
                runs={threadContext?.linked_runs}
                state={threadContext?.sources?.runs ?? "ready"}
                testId="context-linked-runs"
              />
            </div>

            <div>
              <div className="context-section-label">
                Linked Issues & PRs
              </div>
              <div className="context-chips-wrap">
                {threadContext?.linked_issues?.map((iss) => (
                  <span key={iss} className="context-chip">
                    {iss}
                  </span>
                ))}
                {threadContext?.linked_prs?.map((pr) => (
                  <span key={pr} className="context-chip">
                    {pr}
                  </span>
                ))}
                {!threadContext?.linked_issues?.length && !threadContext?.linked_prs?.length && (
                  <div className="context-empty-text">None</div>
                )}
              </div>
            </div>

            <div>
              <div className="context-section-label">
                Linked Code Requests
              </div>
              <div className="context-chips-wrap">
                {threadContext?.linked_code_requests?.map((cr) => (
                  <span key={cr} className="context-chip">
                    {cr}
                  </span>
                ))}
                {!threadContext?.linked_code_requests?.length && (
                  <div className="context-empty-text">None</div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
};

