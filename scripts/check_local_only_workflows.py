#!/usr/bin/env python3
"""Enforce local-only workflows except approved reversible public CI."""

from __future__ import annotations

from pathlib import Path

WORKFLOW_DIR = Path(".github") / "workflows"
BANNED = (
    "ubuntu-latest",
    "windows-latest",
    "macos-latest",
    "force_cloud",
    "mode=cloud",
    "Routing to GitHub-hosted",
    "using GitHub-hosted",
    "runner=ubuntu-latest",
    "runner=windows-latest",
    "runner=macos-latest",
)

# Files allowlisted from the hosted-runner scan. The tripwire workflow
# intentionally runs on a hosted runner; everything else must stay local.
LEGACY_HOSTED_RUNNER_ALLOWLIST = {
    ".github/workflows/local-only-runner-guard.yml",
}


def _is_approved_hybrid(text: str) -> bool:
    return all(token in text for token in ("CI_RUNNER_MODE", "ubuntu-latest", "d-sorg-fleet"))


def main() -> int:
    failures: list[str] = []
    if not WORKFLOW_DIR.exists():
        return 0

    for path in sorted(WORKFLOW_DIR.rglob("*")):
        if path.suffix not in {".yml", ".yaml"}:
            continue

        if path.as_posix() in LEGACY_HOSTED_RUNNER_ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig")
        if path.name == "ci-standard.yml" and _is_approved_hybrid(text):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for token in BANNED:
                if token in line:
                    failures.append(f"{path}:{line_number}: banned hosted-runner token {token!r}")

    if failures:
        print("Unapproved hosted-runner routing found; use local labels or the reversible selector.")
        print("\n".join(failures))
        return 1

    print("Workflow runner routing satisfies the local-only and approved-hybrid policy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
