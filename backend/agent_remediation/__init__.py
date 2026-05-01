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

from .providers import (
    AgentProvider,
    ProviderAvailability,
    PROVIDERS,
    probe_provider_availability,
)
from .policy import (
    SCHEMA_VERSION,
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_ORDER,
    DEFAULT_WORKFLOW_TYPE_RULES,
    LEGACY_WORKFLOW_PATTERNS,
    PROMPT_UNTRUSTED_SYSTEM_INSTRUCTION,
    AttemptRecord,
    FailureContext,
    RemediationPolicy,
    WorkflowTypeRule,
    _as_tuple_strings,
    _default_workflow_type_rules,
    _load_workflow_type_rules,
    _validate_policy_path,
    load_policy,
    save_policy,
    classify_workflow_type,
    build_failure_fingerprint,
    _parse_timestamp,
    _attempts_for_fingerprint,
    _attempts_for_provider,
)
from .planner import (
    DispatchDecision,
    WorkflowHealthEntry,
    WorkflowHealthReport,
    sanitize_for_prompt,
    provider_prompt,
    plan_dispatch,
    inspect_jules_workflows,
)

__all__ = [
    # providers
    "AgentProvider",
    "ProviderAvailability",
    "PROVIDERS",
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
