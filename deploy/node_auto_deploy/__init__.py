"""Node-local pull-based auto-deploy for Runner Dashboard (fleet nodes + hub).

Pure, testable building blocks live in submodules; the cycle orchestrator is
``runner.py``. Shell/systemd wrappers invoke ``deploy/node-auto-deploy.py``.
"""

from node_auto_deploy.ci_gate import ci_gate_for_commit, parse_check_runs_payload
from node_auto_deploy.contracts import AutoDeployEvent, AutoDeployState, DeployResult
from node_auto_deploy.kill_switch import auto_deploy_disabled, kill_switch_reason
from node_auto_deploy.service_scope import ServiceScopeDecision, detect_service_scope
from node_auto_deploy.state import load_state, record_event, save_state
from node_auto_deploy.version import deployed_git_sha, read_deployment_json

__all__ = [
    "AutoDeployEvent",
    "AutoDeployState",
    "DeployResult",
    "ServiceScopeDecision",
    "auto_deploy_disabled",
    "ci_gate_for_commit",
    "deployed_git_sha",
    "detect_service_scope",
    "kill_switch_reason",
    "load_state",
    "parse_check_runs_payload",
    "read_deployment_json",
    "record_event",
    "save_state",
]
