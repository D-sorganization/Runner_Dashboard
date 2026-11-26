"""Guard: every change fragment tracked in ``changes/`` validates (#4393).

The ``changes-fragment`` pre-commit hook only checks the fragments a commit
touches, and CI never ran it over the whole tree, so invalid fragments landed
on ``main``. Every later push whose range contained one (any
``git merge origin/main`` on a PR branch) was then rejected by the hook.
"""

from __future__ import annotations

from pathlib import Path

from scripts import changes_fragment

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_every_tracked_fragment_validates() -> None:
    fragments = changes_fragment.find_fragments(REPO_ROOT)
    assert fragments, "changes/ holds no fragments; the guard would pass vacuously"

    findings = [
        f"changes/{path.name}: {finding}"
        for path in fragments
        for finding in changes_fragment.validate_fragment_file(path)
    ]

    assert not findings, f"{len(findings)} invalid change fragment finding(s):\n" + "\n".join(findings)
