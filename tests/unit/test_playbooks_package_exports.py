"""The ``staff.playbooks`` package names its exports explicitly (#4393)."""

from __future__ import annotations

from pathlib import Path

import staff.playbooks as playbooks
from staff.playbooks import linter, schema

PACKAGE_INIT = Path(playbooks.__file__)


def test_package_init_has_no_wildcard_import() -> None:
    assert "import *" not in PACKAGE_INIT.read_text(encoding="utf-8")


def test_all_lists_every_name_and_each_resolves() -> None:
    assert sorted(playbooks.__all__) == sorted(set(playbooks.__all__))
    for name in playbooks.__all__:
        assert hasattr(playbooks, name), name


def test_public_classes_and_functions_stay_exported() -> None:
    defined = {
        name
        for module in (linter, schema)
        for name, value in vars(module).items()
        if not name.startswith("_") and getattr(value, "__module__", None) == module.__name__
    }
    assert defined <= set(playbooks.__all__), sorted(defined - set(playbooks.__all__))
