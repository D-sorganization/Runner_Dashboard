"""agent_remediation package — re-exports for backwards compatibility (issue #361).

The original agent_remediation.py has been split into three submodules:
  - providers.py  — external provider registry and availability probing
  - policy.py     — policy model, loading, saving, and workflow classification
  - planner.py    — dispatch planning, prompt generation, workflow health

All public names are re-exported here so existing imports continue to work:
  import agent_remediation
  from agent_remediation import plan_dispatch, load_policy, ...
"""

from __future__ import annotations

from .planner import (
    DispatchDecision,
    WorkflowHealthEntry,
    WorkflowHealthReport,
    inspect_jules_workflows,
    plan_dispatch,
    provider_prompt,
    sanitize_for_prompt,
)
from .policy import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_ORDER,
    DEFAULT_WORKFLOW_TYPE_RULES,
    LEGACY_WORKFLOW_PATTERNS,
    PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION,
    SCHEMA_VERSION,
    AttemptRecord,
    FailureContext,
    RemediationPolicy,
    WorkflowTypeRule,
    _attempts_for_fingerprint,
    _attempts_for_provider,
    build_failure_fingerprint,
    classify_workflow_type,
    load_policy,
    save_policy,
)
from .provider_registry import (
    PROVIDER_REGISTRY,
    ProviderEntry,
    by_dashboard_id,
)
from .providers import (
    PROVIDERS,
    AgentProvider,
    ProviderAvailability,
    probe_provider_availability,
)

__all__ = [
    # providers
    "AgentProvider",
    "ProviderAvailability",
    "PROVIDERS",
    "PROVIDER_REGISTRY",
    "ProviderEntry",
    "by_dashboard_id",
    "probe_provider_availability",
    # policy
    "SCHEMA_VERSION",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_PROVIDER_ORDER",
    "DEFAULT_WORKFLOW_TYPE_RULES",
    "LEGACY_WORKFLOW_PATTERNS",
    "PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION",
    "AttemptRecord",
    "FailureContext",
    "RemediationPolicy",
    "WorkflowTypeRule",
    "load_policy",
    "save_policy",
    "classify_workflow_type",
    "build_failure_fingerprint",
    "_attempts_for_fingerprint",
    "_attempts_for_provider",
    # planner
    "DispatchDecision",
    "WorkflowHealthEntry",
    "WorkflowHealthReport",
    "sanitize_for_prompt",
    "provider_prompt",
    "plan_dispatch",
    "inspect_jules_workflows",
]
