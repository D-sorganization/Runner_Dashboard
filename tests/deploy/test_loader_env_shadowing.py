"""Deploy tests must not depend on ``tests`` resolving to this repo's namespace dir (#2912).

``tests/`` here is a namespace package (no ``__init__.py``). A *regular* ``tests``
package anywhere else on ``sys.path`` (e.g. in a toolcache interpreter's
site-packages) wins over it regardless of order, so ``from tests.loader_env import
...`` died at collection with ``No module named 'tests.loader_env'`` on one runner.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = "tests/deploy/test_auto_deploy_ci_regression.py"


def test_deploy_regression_module_collects_despite_shadowing_tests_package(tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    (shadow / "tests").mkdir(parents=True)
    (shadow / "tests" / "__init__.py").write_text("# foreign regular 'tests' package\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(shadow), os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--confcutdir=tests/deploy", "--collect-only", "-q", TARGET],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "No module named 'tests" not in proc.stdout + proc.stderr
