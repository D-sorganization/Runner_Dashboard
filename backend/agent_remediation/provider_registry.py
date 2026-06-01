"""Canonical provider registry table — the ONE source of truth (issue #810).

Before this module, provider identity and metadata were duplicated across at
least three hand-maintained lists:

1. ``agent_remediation.providers.PROVIDERS`` (dashboard underscore ids),
2. the Conductor adapters' ``ProviderMeta`` (hyphen ids, auth/resource/cost),
3. the ad-hoc ``_PROVIDERS_WITH_MODEL_SELECTION`` set in the remediation router.

This table collapses them into a single list of :class:`ProviderEntry` records.
Each entry pairs the dashboard ``dashboard_id`` (underscore) with the Conductor
``conductor_id`` (hyphen) and carries the *static* metadata shared by both the
dashboard UI and the Conductor orchestrator: label, execution/dispatch modes,
auth mode, resource class, capabilities, baseline cost, concurrency, and the
curated model list.

DRY direction (documented): this table is the source; ``providers.py`` derives
its ``PROVIDERS`` dict and ``AgentProvider`` instances *from* this table by
projecting each entry onto the legacy shape. There is exactly one
hand-maintained list.

Design by Contract: :func:`validate_registry` asserts the table's invariants
(unique ids, allowed auth/resource/capability values) at import time so a
malformed edit fails loudly rather than silently shipping a bad contract.

Law of Demeter: entries are flat, frozen dataclasses; consumers read scalar
fields and flat tuples directly and never traverse nested structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from conductor_constants import AUTH_KINDS, CAPABILITIES, RESOURCES


@dataclass(frozen=True, slots=True)
class ProviderEntry:
    """One row of the canonical provider table.

    Attributes:
        dashboard_id: Underscore id used by the dashboard (e.g. ``claude_code_cli``).
        conductor_id: Hyphen id used by the Conductor routing policy
            (e.g. ``claude-cli``).
        label: Human-readable provider label.
        execution_mode: Dashboard execution mode (e.g. ``local_exec``).
        dispatch_mode: Dashboard dispatch mode (e.g. ``github_actions``).
        auth_mode: One of :data:`conductor_constants.AUTH_KINDS`.
        resource: One of :data:`conductor_constants.RESOURCES`.
        capabilities: Conductor capabilities offered (subset of
            :data:`conductor_constants.CAPABILITIES`).
        cost_per_task: Baseline USD cost per task (>= 0).
        max_concurrency: Max in-flight tasks (>= 1).
        models: Curated static model list (CLI providers). Empty for providers
            with no model selection or whose models are fetched live.
        models_endpoint: Live-models endpoint URL, or ``None``.
        availability_probe: Binaries to probe for dashboard availability.
        required_env: Env vars required for dashboard availability.
        credential_id: Probe id in ``routers/credentials.py`` to derive
            login_status from (defaults to ``dashboard_id``).
        setup_hint: One-line setup guidance surfaced to the operator (#812).
        editable: Whether the provider is operator-editable.
        remote: Whether the provider runs a remote session.
        experimental: Whether the provider is gated/experimental.
        notes: Free-form operator notes.
    """

    dashboard_id: str
    conductor_id: str
    label: str
    execution_mode: str
    dispatch_mode: str
    auth_mode: str
    resource: str
    capabilities: tuple[str, ...]
    cost_per_task: float
    max_concurrency: int
    models: tuple[str, ...] = ()
    models_endpoint: str | None = None
    availability_probe: tuple[str, ...] = field(default_factory=tuple)
    required_env: tuple[str, ...] = field(default_factory=tuple)
    credential_id: str = ""
    setup_hint: str = ""
    editable: bool = False
    remote: bool = False
    experimental: bool = False
    notes: str = ""

    @property
    def effective_credential_id(self) -> str:
        """Credential-probe id, defaulting to the dashboard id (LoD accessor)."""
        return self.credential_id or self.dashboard_id


# Live-models endpoint for the local Ollama server (issue #810 item 3).
OLLAMA_TAGS_ENDPOINT = "http://localhost:11434/api/tags"

# Curated, current CLI model lists. ``models.length > 0`` is what now marks a
# provider as supporting model selection (replacing the old hardcoded
# ``_PROVIDERS_WITH_MODEL_SELECTION`` set in the remediation router).
_CLAUDE_MODELS = ("claude-opus-4", "claude-sonnet-4", "claude-haiku-4")
_CODEX_MODELS = ("gpt-5-codex", "gpt-5", "o4-mini")
_GEMINI_MODELS = ("gemini-2.5-pro", "gemini-2.5-flash")


#: The ONE canonical provider table. Order is the dashboard display order.
PROVIDER_REGISTRY: tuple[ProviderEntry, ...] = (
    ProviderEntry(
        dashboard_id="jules_cli",
        conductor_id="jules-cli",
        label="Jules CLI",
        execution_mode="remote_session",
        dispatch_mode="dashboard_local",
        auth_mode="local",
        resource="runner",
        capabilities=("code_edit", "ci_fix", "test_fix"),
        cost_per_task=0.0,
        max_concurrency=1,
        availability_probe=("jules",),
        credential_id="jules_cli",
        setup_hint="Install Jules CLI from jules.google",
        editable=False,
        remote=True,
        notes=("Best for an operator-triggered remote Jules session from the dashboard host."),
    ),
    ProviderEntry(
        dashboard_id="jules_api",
        conductor_id="jules-api",
        label="Jules API",
        execution_mode="remote_session",
        dispatch_mode="github_actions",
        auth_mode="api_key",
        resource="runner",
        capabilities=("code_edit", "ci_fix", "test_fix"),
        cost_per_task=0.0,
        max_concurrency=1,
        required_env=("JULES_API_KEY",),
        credential_id="jules_api",
        setup_hint="Set JULES_API_KEY environment variable",
        editable=False,
        remote=True,
        notes=(
            "Best automation backend for GitHub Actions because the documented Jules CLI login flow is interactive."
        ),
    ),
    ProviderEntry(
        dashboard_id="codex_cli",
        conductor_id="codex-cli",
        label="Codex CLI",
        execution_mode="local_exec",
        dispatch_mode="github_actions",
        auth_mode="github_app",
        resource="runner",
        capabilities=("code_edit", "ci_fix", "test_fix", "lint_fix", "format"),
        cost_per_task=0.02,
        max_concurrency=1,
        models=_CODEX_MODELS,
        availability_probe=("codex",),
        credential_id="codex_cli",
        setup_hint="npm i -g @openai/codex, then `codex login` (subscription, no API key)",
        editable=True,
        notes="Uses `codex exec` for branch-local remediation on a self-hosted runner.",
    ),
    ProviderEntry(
        dashboard_id="claude_code_cli",
        conductor_id="claude-cli",
        label="Claude Code CLI",
        execution_mode="local_exec",
        dispatch_mode="github_actions",
        auth_mode="github_app",
        resource="runner",
        capabilities=(
            "code_edit",
            "code_review",
            "ci_fix",
            "test_fix",
            "refactor",
            "design",
            "security",
            "doc",
        ),
        cost_per_task=0.05,
        max_concurrency=1,
        models=_CLAUDE_MODELS,
        availability_probe=("claude",),
        credential_id="claude_code_cli",
        setup_hint="npm i -g @anthropic-ai/claude-code, then `claude login` (subscription, no API key)",
        editable=True,
        notes=("Uses `claude -p` with auto permissions for branch-local remediation on a self-hosted runner."),
    ),
    ProviderEntry(
        dashboard_id="ollama",
        conductor_id="ollama-local",
        label="Ollama",
        execution_mode="local_analysis",
        dispatch_mode="future",
        auth_mode="local",
        resource="local",
        capabilities=("format", "lint_fix", "label", "comment", "doc"),
        cost_per_task=0.0,
        max_concurrency=2,
        models_endpoint=OLLAMA_TAGS_ENDPOINT,
        availability_probe=("ollama",),
        credential_id="ollama",
        setup_hint="Install from ollama.com and run `ollama serve`",
        editable=False,
        experimental=True,
        notes=(
            "Useful as a low-cost analyzer or triage assistant; code-edit "
            "execution should stay gated until a stronger local agent loop is "
            "selected."
        ),
    ),
    ProviderEntry(
        dashboard_id="gemini_cli",
        conductor_id="gemini-cli",
        label="Gemini CLI",
        execution_mode="local_exec",
        dispatch_mode="github_actions",
        auth_mode="api_key",
        resource="runner",
        capabilities=("code_edit", "refactor", "doc", "lint_fix", "format"),
        cost_per_task=0.0,
        max_concurrency=1,
        models=_GEMINI_MODELS,
        availability_probe=("gemini",),
        required_env=("GOOGLE_API_KEY",),
        credential_id="gemini_cli",
        setup_hint="npm install -g @google/gemini-cli then set GOOGLE_API_KEY",
        editable=True,
        notes=("Uses `gemini` CLI for local remediation and reasoning. Setup: https://aistudio.google.com/app/apikey"),
    ),
    ProviderEntry(
        dashboard_id="cline",
        conductor_id="cline-cli",
        label="Cline",
        execution_mode="local_plugin",
        dispatch_mode="future",
        auth_mode="github_app",
        resource="runner",
        capabilities=("code_edit", "ci_fix", "test_fix", "refactor", "lint_fix", "format"),
        cost_per_task=0.02,
        max_concurrency=1,
        availability_probe=("cline",),
        credential_id="cline",
        setup_hint="Install Cline extension in VS Code: ext install saoudrizwan.claude-dev",
        editable=False,
        experimental=True,
        notes=("Reserved for future plugin-driven local remediation; no stable CLI contract is assumed here yet."),
    ),
)


def validate_registry(entries: tuple[ProviderEntry, ...] = PROVIDER_REGISTRY) -> None:
    """Assert the canonical table's invariants (Design by Contract).

    Postconditions (asserted):
        - dashboard ids are unique and non-empty,
        - conductor ids are unique and non-empty,
        - each ``auth_mode`` is an allowed auth kind,
        - each ``resource`` is an allowed resource,
        - every capability is an allowed conductor capability,
        - ``cost_per_task >= 0`` and ``max_concurrency >= 1``.

    Raises:
        AssertionError: If any invariant is violated.
    """
    dashboard_ids = [e.dashboard_id for e in entries]
    conductor_ids = [e.conductor_id for e in entries]
    assert all(dashboard_ids), "every entry needs a non-empty dashboard_id"
    assert all(conductor_ids), "every entry needs a non-empty conductor_id"
    assert len(dashboard_ids) == len(set(dashboard_ids)), "duplicate dashboard_id"
    assert len(conductor_ids) == len(set(conductor_ids)), "duplicate conductor_id"
    for e in entries:
        assert e.auth_mode in AUTH_KINDS, f"{e.dashboard_id}: bad auth_mode {e.auth_mode!r}"
        assert e.resource in RESOURCES, f"{e.dashboard_id}: bad resource {e.resource!r}"
        assert e.cost_per_task >= 0.0, f"{e.dashboard_id}: negative cost"
        assert e.max_concurrency >= 1, f"{e.dashboard_id}: max_concurrency < 1"
        for cap in e.capabilities:
            assert cap in CAPABILITIES, f"{e.dashboard_id}: bad capability {cap!r}"


def by_dashboard_id() -> dict[str, ProviderEntry]:
    """Return the table keyed by dashboard id (flat lookup, LoD)."""
    return {e.dashboard_id: e for e in PROVIDER_REGISTRY}


# Fail closed at import time: a malformed edit to the table is a hard error.
validate_registry()
