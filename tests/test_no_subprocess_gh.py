"""CI guard: no subprocess gh api calls remain in backend/ (issue #715).

This test will FAIL if anyone re-introduces gh_api() subprocess calls.
"""
import re
from pathlib import Path
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def test_no_gh_api_function_definitions():
    """gh_api() and gh_api_raw() must not be defined in server.py.

    gh_utils.py is the legacy compatibility wrapper and is exempt.
    """
    pattern = re.compile(
        r"^async def gh_api\b|^def gh_api\b|^async def gh_api_raw\b|^def gh_api_raw\b",
        re.MULTILINE,
    )
    # Exempt files that are the canonical legacy wrappers
    exempt = {"gh_utils.py"}
    violations = []
    for py_file in BACKEND_DIR.rglob("*.py"):
        if py_file.name in exempt:
            continue
        text = py_file.read_text(errors="replace")
        if pattern.search(text):
            violations.append(str(py_file))
    assert not violations, f"gh_api() still defined in: {violations}"


def test_no_subprocess_gh_api_calls():
    """No backend code may shell out to 'gh api' via subprocess.

    Matches actual code lines (not comments or docstrings) that invoke
    subprocess with the gh CLI api command.

    gh_utils.py is exempt as the legacy subprocess fallback compatibility layer.
    """
    # Match actual subprocess/run_cmd calls, not comments or docstring mentions
    call_pattern = re.compile(
        r'(?:subprocess\.\w+|run_cmd)\s*\(.*["\']gh["\'].*["\']api["\']'
    )
    # gh_utils.py is the legacy wrapper with a subprocess fallback — exempt
    exempt = {"gh_utils.py"}
    violations = []
    for py_file in BACKEND_DIR.rglob("*.py"):
        if py_file.name in exempt:
            continue
        text = py_file.read_text(errors="replace")
        lines = text.splitlines()
        in_docstring = False
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Track docstring boundaries (triple-quoted strings)
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if not in_docstring:
                    in_docstring = True
                    # Single-line docstring ends on same line with closing triple-quote
                    # after the opening (check if the line has TWO sets of triple-quotes)
                    close_idx = stripped.find('"""', 3) if stripped.startswith('"""') else stripped.find("'''", 3)
                    if close_idx != -1:
                        in_docstring = False
                    continue
                else:
                    in_docstring = False
                    continue
            if in_docstring:
                continue
            # Skip comment lines
            if stripped.startswith("#"):
                continue
            if call_pattern.search(line):
                violations.append(f"{py_file}:{lineno}: {line.strip()}")
    assert not violations, (
        "subprocess gh api calls found:\n" + "\n".join(violations)
    )


def test_no_gh_api_raw_calls():
    """gh_api_raw() must not be called in server.py or routers/.

    gh_utils.py contains the gh_api_raw definition itself — that's the only
    permitted location.
    """
    pattern = re.compile(r"\bgh_api_raw\s*\(")
    # Only check server.py and routers (gh_utils.py defines it, so it's exempt)
    check_files = list((BACKEND_DIR / "routers").rglob("*.py")) + [BACKEND_DIR / "server.py"]
    violations = []
    for py_file in check_files:
        if not py_file.exists():
            continue
        text = py_file.read_text(errors="replace")
        if pattern.search(text):
            violations.append(str(py_file))
    assert not violations, f"gh_api_raw() still called in: {violations}"
