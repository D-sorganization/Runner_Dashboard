"""Deploy-suite collection hook: make ``loader_env`` importable without the ``tests`` package.

The auto-deploy workflow runs ``pytest --confcutdir=tests/deploy`` so ``tests/conftest.py``
never loads, and ``tests`` is only a namespace package here. Any regular ``tests`` package
elsewhere on ``sys.path`` shadows it (``No module named 'tests.loader_env'``). Putting
``tests/`` itself on ``sys.path`` lets deploy modules use ``from loader_env import ...``,
the same single helper module, resolved by a name nothing else defines.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = str(Path(__file__).resolve().parents[1])
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)
