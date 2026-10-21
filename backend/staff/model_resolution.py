"""Resolve the effective model for a staff run plan (issue #2053).

Precedence: request body ``model``, role YAML ``model``, then repo-managed
``config/staff_provider_defaults.json`` and the adapter's ``default_model``.
The runner never relies on a host ``~/.codex/config.toml`` for planning.
"""

from __future__ import annotations

from staff.adapters import ProviderAdapter
from staff.provider_defaults import default_model_for_provider

_UNSET_ROLE_MODELS = frozenset({"", "default"})


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_staff_run_model(
    *,
    requested: str | None,
    role_model: str | None,
    provider_id: str,
    adapter: ProviderAdapter,
) -> str | None:
    """Return the model id to store on the plan and pass to ``build_command``.

    Pre: ``provider_id`` is non-empty.
    Post: when a repo or adapter default exists, the result is non-empty for
    providers that require an explicit CLI model.
    """
    assert provider_id.strip(), "provider_id must be non-empty"  # noqa: S101
    explicit = _normalized(requested)
    if explicit is not None:
        return explicit
    from_role = _normalized(role_model)
    if from_role is not None and from_role.lower() not in _UNSET_ROLE_MODELS:
        return from_role
    from_adapter = _normalized(getattr(adapter, "default_model", None))
    if from_adapter is not None:
        return from_adapter
    return default_model_for_provider(provider_id)
