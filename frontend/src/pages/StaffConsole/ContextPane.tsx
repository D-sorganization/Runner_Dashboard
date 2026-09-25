import React, { useState, useEffect } from "react";
import type { ContextPaneProps } from "./contextTypes";

export const ContextPane: React.FC<ContextPaneProps> = ({
  role,
  threadContext,
  onToggleSchedule,
  className = "",
}) => {
  const [activeTab, setActiveTab] = useState<"role" | "thread">("role");
  const [scheduleEnabled, setScheduleEnabled] = useState<boolean>(role?.schedule?.enabled ?? true);
  const [isToggling, setIsToggling] = useState<boolean>(false);
  const [toggleError, setToggleError] = useState<string | null>(null);

  useEffect(() => {
    if (role?.schedule?.enabled !== undefined) {
      setScheduleEnabled(role.schedule.enabled);
    }
  }, [role?.schedule?.enabled]);

  const handleToggle = async () => {
    if (!role || !onToggleSchedule || isToggling) return;
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

  return (
    <aside
      className={`staff-context-pane ${className}`.trim()}
      aria-label="Context pane"
      style={{
        width: "320px",
        minWidth: "280px",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: "var(--color-bg-surface, #ffffff)",
        borderLeft: "1px solid var(--color-border, #e2e8f0)",
        fontSize: "0.875rem",
      }}
    >
      {/* Tab Header */}
      <div
        role="tablist"
        aria-label="Context views"
        style={{
          display: "flex",
          borderBottom: "1px solid var(--color-border, #e2e8f0)",
          background: "var(--color-bg-muted, #f8fafc)",
        }}
      >
        <button
          role="tab"
          type="button"
          aria-selected={activeTab === "role"}
          onClick={() => setActiveTab("role")}
          style={{
            flex: 1,
            padding: "10px 12px",
            border: "none",
            borderBottom: activeTab === "role" ? "2px solid var(--color-primary, #2563eb)" : "2px solid transparent",
            background: activeTab === "role" ? "var(--color-bg-surface, #ffffff)" : "transparent",
            fontWeight: activeTab === "role" ? 600 : 500,
            color: activeTab === "role" ? "var(--color-primary, #2563eb)" : "var(--color-text-secondary, #64748b)",
            cursor: "pointer",
          }}
        >
          Role
        </button>
        <button
          role="tab"
          type="button"
          aria-selected={activeTab === "thread"}
          onClick={() => setActiveTab("thread")}
          style={{
            flex: 1,
            padding: "10px 12px",
            border: "none",
            borderBottom: activeTab === "thread" ? "2px solid var(--color-primary, #2563eb)" : "2px solid transparent",
            background: activeTab === "thread" ? "var(--color-bg-surface, #ffffff)" : "transparent",
            fontWeight: activeTab === "thread" ? 600 : 500,
            color: activeTab === "thread" ? "var(--color-primary, #2563eb)" : "var(--color-text-secondary, #64748b)",
            cursor: "pointer",
          }}
        >
          Thread
        </button>
      </div>

      {/* Tab Panels */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "14px",
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        }}
      >
        {toggleError && (
          <div
            role="alert"
            style={{
              padding: "8px 10px",
              borderRadius: "6px",
              background: "var(--color-bg-danger-subtle, #fef2f2)",
              color: "var(--color-text-danger, #b91c1c)",
              border: "1px solid var(--color-border-danger, #fecaca)",
              fontSize: "0.75rem",
            }}
          >
            {toggleError}
          </div>
        )}

        {/* ROLE TAB */}
        {activeTab === "role" && role && (
          <div role="tabpanel" aria-label="Role details" style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            {/* Header */}
            <div>
              <div style={{ fontWeight: 700, fontSize: "1rem", color: "var(--color-text-primary, #0f172a)" }}>
                {role.title}
              </div>
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary, #64748b)" }}>
                @{role.name}
              </div>
            </div>

            {/* Mandate */}
            <div>
              <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase" }}>
                Mandate
              </div>
              <p style={{ margin: "4px 0 0 0", color: "var(--color-text-primary, #334155)", lineHeight: 1.4 }}>
                {role.mandate}
              </p>
            </div>

            {/* Schedule Section */}
            {role.schedule && (
              <div
                style={{
                  background: "var(--color-bg-muted, #f8fafc)",
                  padding: "10px",
                  borderRadius: "6px",
                  border: "1px solid var(--color-border, #e2e8f0)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "6px" }}>
                  <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase" }}>
                    Schedule
                  </span>
                  {/* Toggle Switch */}
                  <button
                    type="button"
                    role="switch"
                    aria-checked={scheduleEnabled}
                    aria-label="Toggle schedule"
                    onClick={handleToggle}
                    disabled={isToggling}
                    style={{
                      width: "36px",
                      height: "20px",
                      borderRadius: "10px",
                      background: scheduleEnabled ? "var(--color-primary, #2563eb)" : "var(--color-bg-disabled, #cbd5e1)",
                      position: "relative",
                      border: "none",
                      cursor: isToggling ? "wait" : "pointer",
                      padding: 0,
                      transition: "background 0.2s",
                    }}
                  >
                    <span
                      style={{
                        position: "absolute",
                        top: "2px",
                        left: scheduleEnabled ? "18px" : "2px",
                        width: "16px",
                        height: "16px",
                        borderRadius: "50%",
                        background: "var(--text-on-accent, white)",
                        boxShadow: "0 1px 2px rgba(0,0,0,0.2)",
                        transition: "left 0.2s",
                      }}
                    />
                  </button>
                </div>
                <div style={{ fontSize: "0.8125rem", fontFamily: "monospace" }}>{role.schedule.cron}</div>
                {role.schedule.window && (
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary, #64748b)" }}>
                    Window: {role.schedule.window}
                  </div>
                )}
                <div style={{ fontSize: "0.75rem", color: scheduleEnabled ? "var(--color-success, #16a34a)" : "var(--color-danger, #dc2626)", marginTop: "4px" }}>
                  {scheduleEnabled ? "Active" : "Schedule Paused"}
                </div>
              </div>
            )}

            {/* Providers Section */}
            <div>
              <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase", marginBottom: "6px" }}>
                Providers
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {role.providers?.map((p) => (
                  <span
                    key={p.name}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "5px",
                      padding: "3px 8px",
                      borderRadius: "12px",
                      fontSize: "0.75rem",
                      background: "var(--color-bg-subtle, #f1f5f9)",
                      border: "1px solid var(--color-border, #e2e8f0)",
                    }}
                  >
                    <span
                      style={{
                        width: "6px",
                        height: "6px",
                        borderRadius: "50%",
                        background: p.signed_in ? "var(--accent-green, green)" : "var(--accent-yellow, orange)",
                      }}
                    />
                    {p.name}
                  </span>
                ))}
              </div>
            </div>

            {/* Budget & Spend */}
            {role.budget && (
              <div>
                <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase", marginBottom: "4px" }}>
                  Budget & Spend
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem" }}>
                  <span style={{ color: "var(--color-text-secondary, #64748b)" }}>Today:</span>
                  <span style={{ fontWeight: 600 }}>${role.budget.usd_today.toFixed(2)}</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.8125rem" }}>
                  <span style={{ color: "var(--color-text-secondary, #64748b)" }}>Daily Cap:</span>
                  <span style={{ fontWeight: 600 }}>${role.budget.usd_per_day.toFixed(2)}</span>
                </div>
              </div>
            )}

            {/* Active Runs */}
            {role.active_runs && role.active_runs.length > 0 && (
              <div>
                <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase", marginBottom: "6px" }}>
                  Active Runs
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {role.active_runs.map((r) => (
                    <div
                      key={r.id}
                      style={{
                        padding: "6px 8px",
                        borderRadius: "4px",
                        background: "var(--color-bg-subtle, #f8fafc)",
                        border: "1px solid var(--color-border, #e2e8f0)",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <span style={{ fontFamily: "monospace", fontSize: "0.75rem" }}>{r.id}</span>
                      <span style={{ fontSize: "0.75rem", color: "var(--color-primary, #2563eb)", fontWeight: 500 }}>
                        {r.status}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* THREAD TAB */}
        {activeTab === "thread" && (
          <div role="tabpanel" aria-label="Thread linked items" style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
            <div>
              <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase", marginBottom: "6px" }}>
                Linked Work Items
              </div>
              {threadContext?.linked_work_items?.length ? (
                threadContext.linked_work_items.map((wi) => (
                  <div key={wi.id} style={{ fontSize: "0.8125rem", padding: "4px 0" }}>
                    <span style={{ fontWeight: 600 }}>{wi.id}</span>: <span>{wi.title}</span>
                  </div>
                ))
              ) : (
                <div style={{ color: "var(--color-text-muted, #94a3b8)", fontSize: "0.75rem" }}>None</div>
              )}
            </div>

            <div>
              <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase", marginBottom: "6px" }}>
                Linked Issues & PRs
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {threadContext?.linked_issues?.map((iss) => (
                  <span key={iss} style={{ background: "var(--color-bg-subtle, #f1f5f9)", padding: "2px 6px", borderRadius: "4px", fontSize: "0.75rem" }}>
                    {iss}
                  </span>
                ))}
                {threadContext?.linked_prs?.map((pr) => (
                  <span key={pr} style={{ background: "var(--color-bg-subtle, #f1f5f9)", padding: "2px 6px", borderRadius: "4px", fontSize: "0.75rem" }}>
                    {pr}
                  </span>
                ))}
              </div>
            </div>

            <div>
              <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-muted, #94a3b8)", textTransform: "uppercase", marginBottom: "6px" }}>
                Linked Code Requests
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                {threadContext?.linked_code_requests?.map((cr) => (
                  <span key={cr} style={{ background: "var(--color-bg-subtle, #f1f5f9)", padding: "2px 6px", borderRadius: "4px", fontSize: "0.75rem" }}>
                    {cr}
                  </span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
};
