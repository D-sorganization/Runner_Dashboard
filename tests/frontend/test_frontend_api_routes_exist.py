"""Guard test (issue #1735): every ``/api/*`` literal referenced by the frontend
must correspond to a real backend route.

This is the regression guard for the class of bug fixed by task 3 (a stale
``/api/fleet/health`` literal in ``OperationsDiagnosticsSection.tsx`` pointing
at a route that was renamed to ``/api/fleet/status``) and for the dead-hook
cleanup in task 4 (a hook referencing an endpoint nothing else calls is a sign
the hook itself is dead code).

Approach:
  1. Import ``backend.server``'s ``app`` (same sys.path setup as tests/api) and
     collect every route path.
  2. Walk ``frontend/src/**/*.ts(x)``, skipping ``__tests__`` directories and
     ``*.test.*`` files, and strip comments (JSDoc/block and line) so route
     paths mentioned only in prose don't count as call sites.
  3. Extract every quoted/template-literal string starting with ``/api/``,
     normalize ``${...}`` interpolations and ``{param}`` placeholders to a
     single token, and strip query strings.
  4. Match each normalized literal against the (identically normalized) set of
     backend route paths. Only a base that is extended at runtime (it ends in
     ``/`` or is followed by ``+``) may match by prefix; any other literal must
     match a route exactly, so a dead ``/api/fleet`` is not excused by
     ``/api/fleet/status``.
  5. A small ``ALLOWLIST`` covers genuine false positives with a one-line
     reason each (e.g. an OpenAPI ``description`` field's example URL).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

_PARAM_TOKEN = "__PARAM__"

# Genuine false positives: a literal that appears in the source but is not an
# actual call site (prose examples, OpenAPI schema text, etc.). Each entry
# carries a one-line reason. Keyed by the RAW (unnormalized) literal text.
ALLOWLIST: dict[str, str] = {}

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Matches '...', "...", or `...` literals starting with /api/. Template
# literals may contain ${...} interpolations, which are handled by the
# outer non-greedy match up to the closing backtick.
_LITERAL_RE = re.compile(r"""(?P<q>['"`])(?P<body>/api/[^'"`]*)(?P=q)""")

_BASE_ASSIGN_RE = re.compile(r"base\s*[:=]\s*$", re.IGNORECASE)
_INTERP_RE = re.compile(r"\$\{[^}]*\}")
_BRACE_PARAM_RE = re.compile(r"\{[^{}/]*\}")


def _strip_comments(source: str) -> str:
    source = _BLOCK_COMMENT_RE.sub(" ", source)
    source = _LINE_COMMENT_RE.sub(" ", source)
    return source


def _normalize(path: str) -> str:
    """Strip query strings and collapse interpolation/param placeholders."""
    path = path.split("?", 1)[0]
    path = _INTERP_RE.sub(_PARAM_TOKEN, path)
    path = _BRACE_PARAM_RE.sub(_PARAM_TOKEN, path)
    return path


def _iter_frontend_files() -> list[Path]:
    files: list[Path] = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix not in (".ts", ".tsx"):
            continue
        if "__tests__" in path.parts:
            continue
        if ".test." in path.name:
            continue
        files.append(path)
    return files


def _extract_api_literals() -> list[tuple[str, Path, str, bool]]:
    """Return (raw, file, normalized, is_base) for every /api/ literal.

    ``is_base`` is true when the literal is extended at runtime: it ends in
    ``/``, the next token is ``+``, or it is assigned to a ``*BASE``/``*Base``
    name (``STAFF_BASE = "/api/v1/staff"``, ``apiBase = "/api/v1/staff"``).
    """
    found: list[tuple[str, Path, str, bool]] = []
    for file in _iter_frontend_files():
        text = file.read_text(encoding="utf-8")
        text = _strip_comments(text)
        for match in _LITERAL_RE.finditer(text):
            raw = match.group("body")
            if raw in ALLOWLIST:
                continue
            is_base = (
                raw.endswith("/")
                or text[match.end() :].lstrip().startswith("+")
                or _BASE_ASSIGN_RE.search(text[max(0, match.start() - 60) : match.start()]) is not None
            )
            found.append((raw, file, _normalize(raw), is_base))
    return found


def _backend_route_paths() -> set[str]:
    import server
    from starlette.routing import Route

    def walk(routes) -> list[Route]:
        out: list[Route] = []
        for r in routes:
            if isinstance(r, Route):
                out.append(r)
            elif hasattr(r, "routes"):
                out.extend(walk(r.routes))
        return out

    return {_normalize(r.path) for r in walk(server.app.routes)}


def _segments_match(literal_path: str, backend_path: str) -> bool:
    """True when every segment matches, treating ``__PARAM__`` as a wildcard.

    Covers both directions: a frontend literal that already substituted a
    concrete value for a backend path param (``/api/fleet/control/down`` vs.
    ``/api/fleet/control/{action}``), and a frontend template literal whose
    interpolation lines up with a backend param.
    """
    lit_segs = literal_path.strip("/").split("/")
    be_segs = backend_path.strip("/").split("/")
    if len(lit_segs) != len(be_segs):
        return False
    return all(
        seg == be or seg == _PARAM_TOKEN or be == _PARAM_TOKEN for seg, be in zip(lit_segs, be_segs, strict=True)
    )


def _matches(normalized_literal: str, backend_paths: set[str], is_base: bool) -> bool:
    if any(_segments_match(normalized_literal, bp) for bp in backend_paths):
        return True
    if not is_base:
        return False
    # A base extended at runtime ("/api/reports/" + date): accept when a real
    # backend route is nested under it.
    prefix = normalized_literal if normalized_literal.endswith("/") else normalized_literal + "/"
    return any(bp.startswith(prefix) for bp in backend_paths)


def test_every_frontend_api_literal_matches_a_backend_route() -> None:
    backend_paths = _backend_route_paths()
    literals = _extract_api_literals()

    unmatched: list[str] = []
    for raw, file, normalized, is_base in literals:
        if _matches(normalized, backend_paths, is_base):
            continue
        rel = file.relative_to(REPO_ROOT)
        unmatched.append(f"{rel}: {raw!r} (normalized: {normalized!r})")

    assert not unmatched, (
        "Frontend references /api/* paths with no matching backend route "
        "(add the missing route, fix the stale literal, or add a justified "
        "ALLOWLIST entry in this test):\n" + "\n".join(sorted(set(unmatched)))
    )


def test_guard_has_scanned_a_nonzero_number_of_files() -> None:
    """Sanity check: the walk must actually find frontend source files."""
    assert len(_iter_frontend_files()) > 50


def test_guard_extracts_a_nonzero_number_of_api_literals() -> None:
    """Sanity check: the extraction must actually find /api/ literals."""
    assert len(_extract_api_literals()) > 20
