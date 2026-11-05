"""Go-public prep part 9: merge-queue ref guard jobs on hosted runners (#3303)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
GO_PUBLIC_SELF_HOSTED_FALLBACK = (
    "${{ !github.event.repository.private && vars.CI_RUNNER_MODE != 'local' && 'ubuntu-latest' || 'd-sorg-fleet' }}"
)

WORKFLOWS = (
    "docker-build.yml",
    "ci-auto-deploy-regression.yml",
)


def test_merge_queue_guard_jobs_use_go_public_runner_expression() -> None:
    for name in WORKFLOWS:
        data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        job = data["jobs"]["queue-entry-alive"]
        assert job["runs-on"] == GO_PUBLIC_SELF_HOSTED_FALLBACK, name
