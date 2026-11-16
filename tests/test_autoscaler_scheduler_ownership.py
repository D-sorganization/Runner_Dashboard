"""Autoscaler default pool must not mutate capacity when scheduler owns it (#4311)."""

from __future__ import annotations

import runner_autoscaler as ra


def test_default_pool_mutate_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AUTOSCALER_DEFAULT_POOL_MUTATE", raising=False)
    import importlib

    import autoscaler_config

    importlib.reload(autoscaler_config)
    assert autoscaler_config.DEFAULT_POOL_MUTATE is False


def test_default_pool_mutate_can_be_enabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSCALER_DEFAULT_POOL_MUTATE", "1")
    import importlib

    import autoscaler_config

    importlib.reload(autoscaler_config)
    assert autoscaler_config.DEFAULT_POOL_MUTATE is True
    assert getattr(ra, "DEFAULT_POOL_MUTATE", None) is not None
