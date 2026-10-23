"""Candidate-patch guard (#2573, panel verdict q4 item 8; hardened by #2603, q5).

A scanner's candidate patch is model output written after reading untrusted
code. A patch that changes CI workflows or the scanner contract could weaken the
very checks that review it, so such patches are flagged everywhere they appear
and are never handed to automation (no fix dispatch, no advisory text).

Every line that can name a file in a git diff is read: ``diff --git``,
``---``/``+++``, ``rename from/to`` and ``copy from/to``, with git's C-style
quoting (``"b/.github/x y"``, octal escapes) undone. Because a model could write
a header this parser misreads, any file-header line that mentions a protected
directory after unquoting also counts as protected (fail closed).
"""

from __future__ import annotations

import re

from vuln_scan.findings import normalize_path

PROTECTED_PREFIXES = (".github/", ".oss-scanner/")
_PROTECTED_DIRS = tuple(p.rstrip("/") for p in PROTECTED_PREFIXES)
_FILE_HEADER = re.compile(r"^(?:diff --git |--- |\+\+\+ |(?:rename|copy) (?:from|to) )")
_ONE_PATH = re.compile(r"^(?:--- |\+\+\+ |(?:rename|copy) (?:from|to) )(.*)$")
_GIT = re.compile(r"^diff --git (.*)$")
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "a": "\a", "b": "\b"}


def _unquote(token: str) -> str:
    """Undo git's C-style path quoting; unquoted tokens are returned unchanged."""
    if len(token) < 2 or not (token.startswith('"') and token.endswith('"')):
        return token
    body, out, raw = token[1:-1], bytearray(), 0
    while raw < len(body):
        ch = body[raw]
        if ch == "\\" and raw + 1 < len(body):
            nxt = body[raw + 1]
            octal = body[raw + 1 : raw + 4]
            if re.fullmatch(r"[0-7]{3}", octal):
                out.append(int(octal, 8) & 0xFF)
                raw += 4
                continue
            out.extend(_ESCAPES.get(nxt, nxt).encode("utf-8"))
            raw += 2
            continue
        out.extend(ch.encode("utf-8"))
        raw += 1
    return out.decode("utf-8", errors="replace")


def _strip_side(path: str) -> str:
    path = path.split("\t", 1)[0]  # "--- a/x\t2026-10-23 ..." timestamps
    return path[2:] if path[:2] in ("a/", "b/") else path


def _git_pair(rest: str) -> list[str]:
    """The two paths of a ``diff --git`` line, quoted or not."""
    quoted = re.findall(r'"(?:[^"\\]|\\.)*"|\S+', rest)
    if len(quoted) == 2:
        return [_strip_side(_unquote(t)) for t in quoted]
    return [_strip_side(t) for t in rest.split(" b/", 1)]  # unquoted, spaces in names


def patch_paths(patch: str) -> list[str]:
    """Every file path a unified diff names, in order, without duplicates."""
    found: list[str] = []
    for line in patch.splitlines():
        names: list[str] = []
        git = _GIT.match(line)
        if git:
            names = _git_pair(git.group(1))
        else:
            one = _ONE_PATH.match(line)
            if one:
                names = [_strip_side(_unquote(one.group(1).strip()))]
        for name in names:
            path = normalize_path(name)
            if path and path != "/dev/null" and path not in found:
                found.append(path)
    return found


def _mentions_protected(line: str) -> bool:
    text = _unquote(line) if '"' in line else line
    text = normalize_path(text.replace('"', " ")).lower()
    return any(
        re.search(rf"(?:^|\s)(?:[ab]/)?{re.escape(d)}(?:/|$|\s)", text)
        for d in _PROTECTED_DIRS
    )


def protected_paths(patch: str) -> list[str]:
    """Paths in ``patch`` under ``.github/`` or ``.oss-scanner/`` (case-insensitive).

    A file-header line that mentions a protected directory but did not parse to a
    protected path is reported as ``<unparsed header>: <line>``, so an odd header
    can never smuggle a CI change past the guard.
    """
    out = [p for p in patch_paths(patch) if p.lower().startswith(PROTECTED_PREFIXES)]
    for line in patch.splitlines():
        if _FILE_HEADER.match(line) and _mentions_protected(line):
            if not any(p.lower() in _unquote(line).lower() for p in out):
                out.append(f"<unparsed header>: {line[:200]}")
    return out
