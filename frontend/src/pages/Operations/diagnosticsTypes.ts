export interface DiagnosticsSummary {
  dashboard_pid?: number | string;
  dashboard_memory_mb?: number;
  dashboard_port?: number;
  git_commit?: string;
  is_drifted?: boolean;
  source_commit?: string;
  remote_commit?: string;
  wsl_available?: boolean;
  wsl_status?: string;
}

export interface GitDrift {
  is_drifted?: boolean;
  source_commit?: string;
  remote_commit?: string;
}

export interface RestartResult {
  success: boolean;
  output?: string;
}

export interface LauncherResult {
  message?: string;
  output_dir?: string;
  launchers?: string[];
}

export interface OperationsDiagnosticsSectionProps {
  initialSummary?: DiagnosticsSummary | null;
  onSummaryChange?: (summary: DiagnosticsSummary | null) => void;
}
