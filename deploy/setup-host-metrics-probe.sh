#!/usr/bin/env bash
# Invoked from setup.sh to install the host-metrics probe timer (#4443).
# Kept separate so setup.sh stays under the 540-line audit cap.

setup_install_host_metrics_probe() {
  local script_dir="$1"
  local deploy_dir="$2"
  if [[ -f "${script_dir}/deploy/install-host-metrics-probe.sh" ]]; then
    RUNNER_DASHBOARD_DEPLOY_DIR="${deploy_dir}" \
      bash "${script_dir}/deploy/install-host-metrics-probe.sh" \
      || warn "host-metrics-probe install failed — run: bash ${script_dir}/deploy/install-host-metrics-probe.sh (system scope needs sudo)"
  else
    warn "install-host-metrics-probe.sh not found; skipping host metrics timer"
  fi
}