"""Regenerate docs/codemap artifacts and docs/codemap/freshness-report.json."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPORT_REL = Path("docs/codemap/freshness-report.json")
REPO_MAP_REL = Path("docs/codemap/repo-map.md")
INDEX_REL = Path("docs/codemap.md")
GENERATED_BEGIN = "<!-- codemap:generated:begin -->"
GENERATED_END = "<!-- codemap:generated:end -->"

ENTRYPOINTS: tuple[tuple[str, str], ...] = (
    ("Operator dev server", "start-dashboard.sh"),
    ("FastAPI ASGI app", "backend/server.py"),
    ("Vite SPA root", "frontend/src/main.tsx"),
    ("Fleet CLI entry", "clients/fleet/fleetctl.py"),
)

PACKAGE_ROWS: tuple[tuple[str, str, str], ...] = (
    ("backend/", "FastAPI service, staff runtime, dispatch", "backend/server.py"),
    ("backend/routers/", "REST and WebSocket route modules", "backend/routers/staff.py"),
    ("backend/staff/", "Persona roles, scheduler, Barb relay", "backend/staff/runner.py"),
    ("frontend/", "Vite React operator SPA", "frontend/index.html"),
    ("scripts/", "Fleet guardrails and CI helpers", "scripts/pre_pr.py"),
    ("clients/fleet/", "fleetctl doctor and hub client", "clients/fleet/fleetctl.py"),
    ("tests/", "pytest suites", "tests/conftest.py"),
)


@dataclass(frozen=True)
class SectionSpec:
    name: str
    doc_paths: tuple[str, ...]
    source_paths: tuple[str, ...]


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "repo-map",
        (str(REPO_MAP_REL),),
        ("backend/", "frontend/", "scripts/", "clients/", "start-dashboard.sh", "stop-dashboard.sh"),
    ),
    SectionSpec(
        "architecture",
        ("docs/architecture/",),
        ("backend/server.py", "backend/routers/", "backend/identity.py"),
    ),
    SectionSpec(
        "codemap-index",
        (str(INDEX_REL), str(REPORT_REL)),
        ("docs/codemap/",),
    ),
)


class CodemapRefreshError(RuntimeError):
    pass


def _git(repo_root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_root), *args],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise CodemapRefreshError(f"git {' '.join(args)} failed: {exc.output}") from exc


def head_commit(repo_root: Path) -> str:
    sha = _git(repo_root, "rev-parse", "HEAD")
    if len(sha) != 40:
        raise CodemapRefreshError(f"unexpected HEAD sha: {sha!r}")
    return sha


def last_commit_touching(repo_root: Path, paths: Sequence[str]) -> str | None:
    existing = [p for p in paths if (repo_root / p).exists()]
    if not existing:
        return None
    try:
        return _git(repo_root, "log", "-1", "--format=%H", "--", *existing)
    except CodemapRefreshError:
        return None


def _is_ancestor(repo_root: Path, older: str, newer: str) -> bool:
    """True when ``older`` is ``newer`` or reachable from it (docs caught up)."""
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", older, newer],
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def stale_sections(repo_root: Path, *, section_overrides: Mapping[str, Mapping[str, object]] | None = None) -> list[str]:
    overrides = section_overrides or {}
    stale: list[str] = []
    for spec in SECTIONS:
        meta = dict(overrides.get(spec.name) or {})
        source_tip = meta.get("source_commit") or last_commit_touching(repo_root, spec.source_paths)
        doc_tip = meta.get("doc_commit") or last_commit_touching(repo_root, spec.doc_paths)
        if source_tip is None or doc_tip is None or not _is_ancestor(repo_root, str(source_tip), str(doc_tip)):
            stale.append(spec.name)
    return stale


def render_repo_map(repo_root: Path) -> str:
    router_count = sum(1 for _ in (repo_root / "backend" / "routers").glob("*.py"))
    lines = [
        "# Runner_Dashboard repository map",
        "",
        GENERATED_BEGIN,
        "",
        "## Entry points",
        "",
        "| Surface | Path |",
        "| --- | --- |",
    ]
    for label, path in ENTRYPOINTS:
        flag = "yes" if (repo_root / path).exists() else "missing"
        lines.append(f"| {label} | `{path}` ({flag}) |")
    lines.extend(["", "## Main packages", "", "| Path | Role | Anchor |", "| --- | --- | --- |"])
    for path, role, anchor in PACKAGE_ROWS:
        flag = "ok" if (repo_root / anchor).exists() else "missing"
        lines.append(f"| `{path}` | {role} | `{anchor}` ({flag}) |")
    lines.extend(
        [
            "",
            "## HTTP routers",
            "",
            f"`backend/routers/` has **{router_count}** modules mounted from `backend/server.py`.",
            "See `docs/architecture/C4.md` for capability traceability.",
            "",
            GENERATED_END,
            "",
        ]
    )
    return "\n".join(lines)


def write_repo_map(repo_root: Path) -> None:
    target = repo_root / REPO_MAP_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    generated = render_repo_map(repo_root)
    if target.is_file() and GENERATED_BEGIN in target.read_text(encoding="utf-8"):
        text = target.read_text(encoding="utf-8")
        before, rest = text.split(GENERATED_BEGIN, 1)
        _, after = rest.split(GENERATED_END, 1)
        target.write_text(before + generated + after, encoding="utf-8")
        return
    target.write_text(generated, encoding="utf-8")


def build_report(repo_root: Path) -> dict[str, object]:
    commit = head_commit(repo_root)
    now = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    sections = {
        spec.name: {
            "doc_paths": list(spec.doc_paths),
            "source_paths": list(spec.source_paths),
            "doc_commit": last_commit_touching(repo_root, spec.doc_paths),
            "source_commit": last_commit_touching(repo_root, spec.source_paths),
        }
        for spec in SECTIONS
    }
    return {
        "repository": "Runner_Dashboard",
        "generated_at": now,
        "commit_sha": commit,
        "report_path": str(REPORT_REL).replace("\\", "/"),
        "refresh_command": "python scripts/codemap_refresh.py",
        "sections": sections,
        "stale_sections": stale_sections(repo_root),
    }


def write_report(repo_root: Path, payload: Mapping[str, object]) -> None:
    path = repo_root / REPORT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def refresh(repo_root: Path) -> dict[str, object]:
    repo_root = repo_root.resolve()
    if not (repo_root / ".git").exists():
        raise CodemapRefreshError(f"not a git repository: {repo_root}")
    for anchor in ("backend/server.py", "start-dashboard.sh"):
        if not (repo_root / anchor).exists():
            raise CodemapRefreshError(
                f"not a Runner_Dashboard checkout: {anchor} is missing under {repo_root}"
            )
    commit = head_commit(repo_root)
    write_repo_map(repo_root)
    payload = dict(build_report(repo_root))
    sections = dict(payload["sections"])
    for name in ("repo-map", "codemap-index"):
        row = dict(sections[name])
        row["doc_commit"] = commit
        if name == "codemap-index":
            row["source_commit"] = commit
        sections[name] = row
    payload["sections"] = sections
    payload["stale_sections"] = stale_sections(repo_root, section_overrides=sections)
    write_report(repo_root, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    try:
        if args.check_only:
            stale = build_report(root).get("stale_sections") or []
            if stale:
                sys.stderr.write(f"STALE sections: {', '.join(stale)}\n")
                return 1
            sys.stdout.write("Codemap sections are fresh.\n")
            return 0
        payload = refresh(root)
        stale = payload.get("stale_sections") or []
        sys.stdout.write(
            f"Wrote {REPORT_REL} at {payload['commit_sha']} "
            f"({len(stale)} stale: {', '.join(stale) or 'none'})\n"
        )
        return 0
    except CodemapRefreshError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
