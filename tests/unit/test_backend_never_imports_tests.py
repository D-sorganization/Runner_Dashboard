"""Production code must not import the ``tests`` package (#1718).

``GET /api/v1/staff/routing/eval`` imported ``tests.staff.routing_eval.engine``;
deployed hubs ship ``backend/`` only, so the route was a 500 in production.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
_TESTS_IMPORT = re.compile(r"^\s*(from|import)\s+tests(\.|\s|$)", re.MULTILINE)


@pytest.mark.unit
def test_no_backend_module_imports_the_tests_package() -> None:
    offenders = [
        str(path.relative_to(BACKEND))
        for path in BACKEND.rglob("*.py")
        if "tests" not in path.relative_to(BACKEND).parts and _TESTS_IMPORT.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


@pytest.mark.unit
def test_routing_evaluation_engine_ships_with_the_backend() -> None:
    engine = importlib.import_module("staff.routing_eval.engine")
    assert callable(engine.run_evaluation)
