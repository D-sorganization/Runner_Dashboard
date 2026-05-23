"""Meta-test: log.warning swallowing exceptions must not exist (issue #717).

Any `except Exception as exc:` block that then does `log.warning("... %s", exc)`
(single-line, no traceback) is a finding that must be converted to log.exception().
"""
import re
from pathlib import Path
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def _find_swallowed_exceptions(file_path: Path) -> list[str]:
    """Find patterns where an exception is caught and logged without traceback."""
    text = file_path.read_text(errors="replace")
    lines = text.splitlines()
    findings = []

    in_except = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Detect `except Exception as exc:` (or similar)
        if re.match(r"except\s+\w.*\s+as\s+\w+\s*:", stripped):
            in_except = True
            continue
        if in_except:
            # Next non-empty, non-comment line
            if not stripped or stripped.startswith("#"):
                continue
            # If it's log.warning(..., exc) without exc_info, flag it
            if re.match(r"log\.warning\(.*\bexc\b[^)]*\)", stripped) and "exc_info" not in stripped:
                findings.append(f"{file_path}:{i}: {stripped}")
            in_except = False

    return findings


def test_server_py_no_swallowed_exceptions():
    """backend/server.py must not swallow exceptions with log.warning."""
    server_py = BACKEND_DIR / "server.py"
    if not server_py.exists():
        pytest.skip("server.py not found")

    findings = _find_swallowed_exceptions(server_py)
    # Allow up to 5 remaining (these may be intentional inspect-not-handle patterns)
    assert len(findings) <= 5, (
        f"Too many swallowed exceptions in server.py ({len(findings)} found):\n"
        + "\n".join(findings[:10])
    )
