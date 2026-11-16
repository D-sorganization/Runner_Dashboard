"""CI/local gate for Fleet Agent API ops-MCP subset (#4305).

Runs the focused pytest module; wired into standard CI via the full ``tests/`` suite.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    cmd = [sys.executable, "-m", "pytest", "tests/test_ops_mcp_conformance.py", "-q", "--tb=short"]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
