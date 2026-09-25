export interface DeploymentMachine {
  name: string;
  display_name?: string;
  rollout_state?: string;
  rollout_label?: string;
  desired_version?: string;
  deployed_version?: string;
  drift_status?: {
    severity?: string;
    update_available?: boolean;
    message?: string;
  };
  last_health_check?: string | null;
  last_rollback?: string | null;
}

export interface DeploymentStateData {
  expected_version?: string;
  rollout_state?: {
    status?: string;
    summary?: string;
    machines_attention?: number;
    machines_online?: number;
    machines_total?: number;
  };
  drift?: {
    current?: string;
    expected?: string;
    message?: string;
  };
  machines?: DeploymentMachine[];
}

export interface OrchestrationMachine {
  name: string;
  display_name?: string;
  role?: string;
  online?: boolean;
  runner_count?: number | null;
  busy_runners?: number | null;
  cpu_percent?: number | null;
  memory_percent?: number | null;
  last_ping?: string | null;
}

export interface OrchestrationAuditEntry {
  audit_id?: string | number;
  recorded_at?: string | null;
  orchestration_type?: string;
  machine?: string;
  deploy_action?: string;
  workflow?: string;
  requested_by?: string;
  decision?: string;
}

export interface FleetOrchestrationData {
  machines?: OrchestrationMachine[];
  audit_log?: OrchestrationAuditEntry[];
  online_count?: number;
  total_count?: number;
}

export interface OperationsDeploySectionProps {
  initialDeployData?: DeploymentStateData | null;
  initialOrchData?: FleetOrchestrationData | null;
  onDeployDataChange?: (data: DeploymentStateData | null) => void;
  onOrchDataChange?: (data: FleetOrchestrationData | null) => void;
}
