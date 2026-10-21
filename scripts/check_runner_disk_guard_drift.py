"""Verify vendored runner disk guard files match Repository_Management upstream."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM_REPO = "D-sorganization/Repository_Management"
MANIFEST_PATH = Path("tests/contracts/runner_disk_guard_upstream.json")
VENDORED_PATHS = (
    "shared_scripts/runner_disk_guard_config.py",
    "config/runner_disk_guard.yaml",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _github_api_get(url: str, token: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _fetch_upstream_file(repo: str, ref: str, path: str, token: str) -> bytes:
    import base64

    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
    payload = json.loads(_github_api_get(url, token))
    content = payload.get("content")
    if not isinstance(content, str):
        raise RuntimeError(f"unexpected GitHub contents payload for {path}")
    return base64.b64decode(content)


def resolve_upstream_ref(token: str, *, ref: str | None, pr_number: int | None) -> str:
    if ref:
        return ref
    if pr_number is not None:
        url = f"https://api.github.com/repos/{UPSTREAM_REPO}/pulls/{pr_number}"
        try:
            payload = json.loads(_github_api_get(url, token))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"cannot read RM PR #{pr_number}: HTTP {exc.code}") from exc
        merge_sha = payload.get("merge_commit_sha")
        if not merge_sha:
            raise RuntimeError(f"PR #{pr_number} has no merge_commit_sha")
        return str(merge_sha)
    return "main"


def compare_upstream(
    *,
    token: str,
    ref: str,
    repo_root: Path,
) -> list[str]:
    errors: list[str] = []
    for rel in VENDORED_PATHS:
        local = (repo_root / rel).read_bytes()
        try:
            upstream = _fetch_upstream_file(UPSTREAM_REPO, ref, rel, token)
        except urllib.error.HTTPError as exc:
            errors.append(f"failed to fetch upstream {rel} at {ref}: HTTP {exc.code}")
            continue
        if local != upstream:
            errors.append(
                f"{rel} differs from {UPSTREAM_REPO}@{ref} "
                f"(local sha256={_sha256_bytes(local)}, upstream sha256={_sha256_bytes(upstream)})"
            )
    return errors


def write_manifest(ref: str, repo_root: Path) -> None:
    files = {rel: _sha256_bytes((repo_root / rel).read_bytes()) for rel in VENDORED_PATHS}
    manifest = {
        "upstream_repository": UPSTREAM_REPO,
        "upstream_ref": ref,
        "sha256": files,
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def verify_manifest(repo_root: Path) -> list[str]:
    if not MANIFEST_PATH.is_file():
        return [f"missing manifest {MANIFEST_PATH}"]
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    expected = manifest.get("sha256")
    if not isinstance(expected, dict):
        return ["manifest sha256 field must be a mapping"]
    errors: list[str] = []
    for rel, digest in expected.items():
        path = repo_root / str(rel)
        if not path.is_file():
            errors.append(f"missing vendored file {rel}")
            continue
        actual = _sha256_bytes(path.read_bytes())
        if actual != digest:
            errors.append(f"{rel} sha256 {actual} != manifest {digest}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--ref", help="upstream git ref (default: main or PR merge sha)")
    parser.add_argument("--pr", type=int, default=2230, help="RM PR to read merge_commit_sha from")
    parser.add_argument("--manifest-only", action="store_true", help="verify tests/contracts manifest only")
    parser.add_argument("--write-manifest", action="store_true", help="refresh manifest from local files")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.write_manifest:
        ref = args.ref or "main"
        write_manifest(ref, repo_root)
        print(f"wrote {MANIFEST_PATH} for upstream_ref={ref}")
        return 0

    manifest_errors = verify_manifest(repo_root)
    if manifest_errors and not args.manifest_only:
        for err in manifest_errors:
            print(err, file=sys.stderr)

    if args.manifest_only:
        if manifest_errors:
            for err in manifest_errors:
                print(err, file=sys.stderr)
            return 1
        print("manifest matches vendored files")
        return 0

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("no GH_TOKEN/GITHUB_TOKEN; skipping live upstream compare", file=sys.stderr)
        return 1 if manifest_errors else 0

    try:
        ref = resolve_upstream_ref(token, ref=args.ref, pr_number=None if args.ref else args.pr)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1 if manifest_errors else 0
    errors = compare_upstream(token=token, ref=ref, repo_root=repo_root)
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        print(f"upstream ref: {UPSTREAM_REPO}@{ref}", file=sys.stderr)
        return 1
    print(f"vendored runner disk guard matches {UPSTREAM_REPO}@{ref}")
    if manifest_errors:
        print("warning: manifest out of date; run with --write-manifest after sync", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
