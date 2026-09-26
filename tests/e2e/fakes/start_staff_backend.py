#!/usr/bin/env python3
"""Start the dashboard backend against fake provider CLIs for the Staff Console e2e suite (#1341).

Everything the backend writes (conversations, runs, identity, worktrees) goes
under a fresh state directory, and ``PATH`` is reduced to the fakes plus the
system directories, so the suite never reaches a real provider, a real repo
checkout or the developer's own dashboard state.

Usage: ``python tests/e2e/fakes/start_staff_backend.py --port 5001 [--state DIR]``

Identity: two principals are seeded, and their bearer tokens are the constants
in ``tests/e2e/fakes/identity.json`` (fixture values, valid only against this
throwaway state directory). ``operator`` holds the ``operator`` preset plus
``staff.approve``; ``viewer`` can read but not chat.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml

FAKES = Path(__file__).resolve().parent
REPO = FAKES.parents[2]
IDENTITY = json.loads((FAKES / "identity.json").read_text(encoding="utf-8"))


def seed_identity(identity_dir: Path) -> None:
    """Write principals.yml and tokens.yml for the fixture principals.

    Post: every principal in identity.json has exactly one token whose sha256
    matches its fixture bearer value.
    """
    identity_dir.mkdir(parents=True, exist_ok=True)
    principals = [
        {"id": p["id"], "type": "human", "name": p["name"], "roles": p["roles"], "scopes": p["scopes"]}
        for p in IDENTITY["principals"]
    ]
    tokens = [
        {
            "token_hash": hashlib.sha256(p["token"].encode()).hexdigest(),
            "principal_id": p["id"],
            "created_at": 0.0,
            "expires_at": None,
            "name": f"e2e-{p['id']}",
        }
        for p in IDENTITY["principals"]
    ]
    (identity_dir / "principals.yml").write_text(yaml.safe_dump({"principals": principals}), encoding="utf-8")
    (identity_dir / "tokens.yml").write_text(yaml.safe_dump({"tokens": tokens}), encoding="utf-8")


def backend_env(state: Path, port: int) -> dict[str, str]:
    """The environment the backend runs under. Pre: ``state`` exists."""
    assert state.is_dir(), state  # noqa: S101
    system_path = os.pathsep.join(p for p in ("/usr/local/bin", "/usr/bin", "/bin") if Path(p).is_dir())
    return {
        **{k: v for k, v in os.environ.items() if k in ("LANG", "LC_ALL", "TZ", "SYSTEMROOT", "TEMP", "TMP")},
        "PATH": os.pathsep.join([str(FAKES / "bin"), system_path or os.environ.get("PATH", "")]),
        "HOME": str(state / "home"),
        "XDG_CONFIG_HOME": str(state / "xdg"),
        "DASHBOARD_IDENTITY_DIR": str(state / "identity"),
        "DASHBOARD_PORT": str(port),
        "GH_TOKEN": "e2e-not-a-token",
        "STAFF_ROLES_DIR": str(FAKES / "roles"),
        "STAFF_KNOWLEDGE_DIR": str(state / "knowledge"),
        "STAFF_REPOS_ROOT": str(state / "repos"),
        "STAFF_WORKTREES_ROOT": str(state / "worktrees"),
        "STAFF_RM_ROOT": str(state / "rm"),
        "PYTHONUNBUFFERED": "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "5001")))
    parser.add_argument("--state", type=Path, default=None, help="state directory (default: a new temp dir)")
    args = parser.parse_args()

    state = args.state or Path(tempfile.mkdtemp(prefix="rd-staff-e2e-"))
    for sub in ("home", "xdg", "knowledge", "repos", "worktrees", "rm"):
        (state / sub).mkdir(parents=True, exist_ok=True)
    seed_identity(state / "identity")
    env = backend_env(state, args.port)
    print(f"staff e2e backend: state={state} port={args.port}", flush=True)
    os.chdir(REPO / "backend")
    os.execve(sys.executable, [sys.executable, "server.py"], env)
    return 0  # unreachable: execve replaces the process


if __name__ == "__main__":
    sys.exit(main())
