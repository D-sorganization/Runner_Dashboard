"""Path-stable wrapper around the detect-secrets pre-commit hook.

detect-secrets 1.5.0 converts baseline paths to the local OS separator
(``detect_secrets.util.path.convert_local_os_path``), so on Windows every run
that touches ``.secrets.baseline`` rewrites it with backslash paths, a fresh
``generated_at`` and CRLF line endings, then exits 3. That blocks every local
commit/push from Windows hosts even on a clean ``main``.

This wrapper runs the upstream hook unchanged, then normalises the rewritten
baseline back to forward slashes. If the only difference from the committed
baseline was path separators / ``generated_at``, the original bytes are
restored and the hook passes. Genuine baseline changes (new line numbers,
removed findings) are still written (with ``/`` paths, LF endings) and still
exit 3, so the audit contract is unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASELINE_UPDATED = 3
_VOLATILE_KEYS = frozenset({"generated_at"})


def to_posix(path: str) -> str:
    return path.replace("\\", "/")


def normalize_results(results: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Return ``results`` with every filename key and ``filename`` field in POSIX form."""
    normalized: dict[str, list[dict[str, Any]]] = {}
    for filename, findings in results.items():
        fixed = [{**f, "filename": to_posix(f["filename"])} if "filename" in f else dict(f) for f in findings]
        normalized.setdefault(to_posix(filename), []).extend(fixed)
    return normalized


def _stable_view(baseline: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in baseline.items() if k not in _VOLATILE_KEYS}


def reconcile(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    """Compare a hook-rewritten baseline with the committed one.

    Returns ``None`` when they differ only by path separators or volatile keys
    (nothing to commit). Otherwise returns ``after`` normalised to POSIX paths,
    with results ordered like ``before`` so the diff stays minimal.
    """
    fixed = {**after, "results": normalize_results(after.get("results", {}))}
    if _stable_view(fixed) == _stable_view(before):
        return None
    order = list(before.get("results", {}))
    results = fixed["results"]
    ranked = sorted(results, key=lambda k: (order.index(k) if k in order else len(order), k))
    fixed["results"] = {k: results[k] for k in ranked}
    return fixed


def baseline_path(argv: list[str]) -> Path | None:
    for i, arg in enumerate(argv):
        if arg == "--baseline" and i + 1 < len(argv):
            return Path(argv[i + 1])
        if arg.startswith("--baseline="):
            return Path(arg.split("=", 1)[1])
    return None


def settle_baseline(path: Path, original: bytes) -> int:
    """Post-process a baseline the upstream hook rewrote; return the hook exit code."""
    after = json.loads(path.read_text(encoding="utf-8"))
    fixed = reconcile(json.loads(original.decode("utf-8")), after)
    if fixed is None:
        path.write_bytes(original)
        return 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(fixed, indent=2) + "\n")
    return BASELINE_UPDATED


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from detect_secrets.pre_commit_hook import main as upstream_main

    path = baseline_path(args)
    original = path.read_bytes() if path is not None and path.is_file() else None
    code = upstream_main(args)
    if code != BASELINE_UPDATED or path is None or original is None:
        return code
    return settle_baseline(path, original)


if __name__ == "__main__":
    raise SystemExit(main())
