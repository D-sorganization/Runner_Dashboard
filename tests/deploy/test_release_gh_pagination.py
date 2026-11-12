"""Exercise the release gate with older gh pagination and partial failures."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


def _run_gate(
    tmp_path: Path, *, fail_after_first_page: bool = False, malformed_page: bool = False
) -> subprocess.CompletedProcess[str]:
    pages = [
        {
            "total_count": 3,
            "check_runs": [
                {
                    "id": index,
                    "head_sha": SHA,
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-10-22T00:00:00Z",
                    "check_suite": {"id": index},
                    "app": {"id": 1, "slug": "github-actions"},
                }
            ],
        }
        for index, name in [(1, "first-page"), (2, 'second "page" 雪'), (3, "third-page")]
    ]
    page_file = tmp_path / "pages.json"
    page_file.write_text(json.dumps(pages), encoding="utf-8")
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if '--slurp' in args or '--paginate' not in args or not any('filter=all' in x for x in args):\n"
        "    raise SystemExit(2)\n"
        "pages = json.load(open(os.environ['TEST_PAGES']))\n"
        "print(json.dumps(pages[0], indent=2, ensure_ascii=False))\n"
        "if os.environ.get('TEST_FAIL_AFTER_FIRST') == '1':\n"
        "    Path(os.environ['TEST_PAGE_SEEN']).touch()\n"
        "    raise SystemExit(1)\n"
        "if os.environ.get('TEST_MALFORMED') == '1':\n"
        "    print('{\"total_count\":')\n"
        "    raise SystemExit(0)\n"
        "for page in pages[1:]:\n"
        "    print(json.dumps(page, indent=2, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    fake_jq = tmp_path / "jq"
    fake_jq.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_jq.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "example/repo",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "RUNNER_TEMP": str(tmp_path),
        "GATE_ATTEMPTS": "1",
        "TEST_PAGES": str(page_file),
        "TEST_PAGE_SEEN": str(tmp_path / "first-page-seen"),
        "TEST_FAIL_AFTER_FIRST": "1" if fail_after_first_page else "0",
        "TEST_MALFORMED": "1" if malformed_page else "0",
    }
    return subprocess.run(
        [
            "bash",
            str(ROOT / "deploy/publish-main-ci-release.sh"),
            "--sha",
            SHA,
            "--committer-date",
            "2026-10-22T00:00:00Z",
            "--gate-only",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_gate_accepts_old_gh_and_preserves_all_pages(tmp_path: Path) -> None:
    result = _run_gate(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "3 check name(s) succeeded" in result.stdout
    assert "ok=true" in (tmp_path / "output").read_text(encoding="utf-8")


def test_release_gate_fails_closed_when_later_page_fetch_fails(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, fail_after_first_page=True)
    assert result.returncode != 0
    assert (tmp_path / "first-page-seen").exists()
    assert "ok=false" in (tmp_path / "output").read_text(encoding="utf-8")


def test_release_gate_fails_closed_on_truncated_page(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, malformed_page=True)
    assert result.returncode != 0
    assert "ok=false" in (tmp_path / "output").read_text(encoding="utf-8")
