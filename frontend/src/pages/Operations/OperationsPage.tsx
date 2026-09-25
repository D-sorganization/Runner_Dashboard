import React, { useEffect, useState } from "react";
import {
  OperationsStatusBanner,
  type OperationsDeploySummary,
  type OperationsAdmissionSummary,
  type OperationsRunnerHoursSummary,
  type OperationsScheduledWorkflowsSummary,
  type OperationsDiagnosticsSummary,
} from "./OperationsStatusBanner";
import { OperationsDeploySection, type DeploymentStateData } from "./OperationsDeploySection";
import { OperationsAdmissionSection, type QueueStatus } from "./OperationsAdmissionSection";
import { OperationsRunnerHoursSection, type RunnerScheduleData } from "./OperationsRunnerHoursSection";
import { OperationsScheduledWorkflowsSection, type ScheduledWorkflowsData } from "./OperationsScheduledWorkflowsSection";
import { OperationsDiagnosticsSection, type DiagnosticsSummary } from "./OperationsDiagnosticsSection";

export function OperationsPage(): React.ReactElement {
  const [deploySummary, setDeploySummary] = useState<OperationsDeploySummary | undefined>();
  const [admissionSummary, setAdmissionSummary] = useState<OperationsAdmissionSummary | undefined>();
  const [runnerHoursSummary, setRunnerHoursSummary] = useState<OperationsRunnerHoursSummary | undefined>();
  const [scheduledWorkflowsSummary, setScheduledWorkflowsSummary] = useState<OperationsScheduledWorkflowsSummary | undefined>();
  const [diagnosticsSummary, setDiagnosticsSummary] = useState<OperationsDiagnosticsSummary | undefined>();

  // Smooth scroll to section when hash is set
  const scrollToHash = (hashStr?: string) => {
    const target = (hashStr || (typeof window !== "undefined" ? window.location.hash : "")).replace(/^#/, "");
    if (target && typeof document !== "undefined") {
      const el = document.getElementById(target);
      if (el) {
        el.scrollIntoView({ behavior: "smooth" });
      }
    }
  };

  useEffect(() => {
    scrollToHash();
    const handleHashChange = () => scrollToHash();
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const handleJumpToSection = (sectionId: string) => {
    if (typeof window !== "undefined") {
      window.location.hash = `#${sectionId}`;
      scrollToHash(sectionId);
    }
  };

  const handleDeployDataChange = (data: DeploymentStateData | null) => {
    if (!data) return;
    setDeploySummary({
      expectedVersion: data.expected_version,
      rolloutStatus: data.rollout_state?.status,
      driftCount: data.rollout_state?.machines_attention,
    });
  };

  const handleAdmissionStatusChange = (status: QueueStatus | null) => {
    if (!status) return;
    setAdmissionSummary({
      mode: status.mode,
      activeLeases: status.active_leases,
      workPlanned: status.work?.planned,
    });
  };

  const handleRunnerHoursChange = (data: RunnerScheduleData) => {
    setRunnerHoursSummary({
      desiredRunners: data.state?.desired ?? 0,
      onlineRunners: data.state?.online ?? 0,
    });
  };

  const handleScheduledWorkflowsChange = (data: ScheduledWorkflowsData) => {
    setScheduledWorkflowsSummary({
      totalCount: data.scheduled_workflow_count ?? data.repositories?.reduce(
        (acc, r) => acc + (r.scheduled_workflow_count || r.workflows?.length || 0),
        0,
      ) ?? 0,
    });
  };

  const handleDiagnosticsChange = (data: DiagnosticsSummary | null) => {
    if (!data) return;
    setDiagnosticsSummary({
      gitDrift: data.is_drifted,
      memoryMb: data.dashboard_memory_mb,
    });
  };

  return (
    <div
      style={{
        maxWidth: "1400px",
        margin: "0 auto",
        padding: "1rem",
        color: "var(--text-primary, #c9d1d9)",
      }}
    >
      <OperationsStatusBanner
        onJumpToSection={handleJumpToSection}
        deploySummary={deploySummary}
        admissionSummary={admissionSummary}
        runnerHoursSummary={runnerHoursSummary}
        scheduledWorkflowsSummary={scheduledWorkflowsSummary}
        diagnosticsSummary={diagnosticsSummary}
      />

      <OperationsDeploySection onDeployDataChange={handleDeployDataChange} />

      <OperationsAdmissionSection onStatusChange={handleAdmissionStatusChange} />

      <OperationsRunnerHoursSection onDataChange={handleRunnerHoursChange} />

      <OperationsScheduledWorkflowsSection onDataChange={handleScheduledWorkflowsChange} />

      <OperationsDiagnosticsSection onSummaryChange={handleDiagnosticsChange} />
    </div>
  );
}

export default OperationsPage;
