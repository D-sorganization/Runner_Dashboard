"""Real ``subprocess.Popen`` captured before ``tests/conftest.py`` monkeypatches it."""

from __future__ import annotations

import subprocess

# Used by SpawnCounter tests and by the autouse _no_real_provider_cli guard.
REAL_SUBPROCESS_POPEN = subprocess.Popen
