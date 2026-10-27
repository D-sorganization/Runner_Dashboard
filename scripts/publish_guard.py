"""Publish guard: allowlist + forbidden path/pattern check (#3080, docs/architecture/publish-guard.md)."""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "config" / "publish" / "publish-allowlist.txt"
PATTERNS = ROOT / "config" / "publish" / "forbidden-patterns.json"
MODES = ("report", "enforce")
MODE_ENV = "RD_PUBLISH_GUARD_MODE"
MAX_SCAN_BYTES = 2_000_000


@dataclass(frozen=True)
class Finding:
    path: str
    category: str
    line: int = 0

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.category}" if self.line else f"{self.path}: {self.category}"


@dataclass(frozen=True)
class Rules:
    allow: tuple[str, ...]
    path_globs: dict[str, tuple[str, ...]]
    content: dict[str, re.Pattern[str]]


def resolve_mode(cli_mode: str | None, env: dict[str, str] | None = None) -> str:
    env = dict(os.environ) if env is None else env
    mode = cli_mode or env.get(MODE_ENV, "") or "report"
    if mode not in MODES:
        raise SystemExit(f"publish guard: mode must be one of {MODES}")
    return mode


@functools.lru_cache(maxsize=512)
def _glob_re(pattern: str) -> re.Pattern[str]:
    """Compile a glob where ``*`` stays within one directory and ``**`` spans them."""
    tokens = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}
    return re.compile("".join(tokens.get(p, re.escape(p)) for p in re.split(r"(\*\*/|\*\*|\*)", pattern)))


def load_rules(allowlist: Path = ALLOWLIST, patterns: Path = PATTERNS, extra: Path | None = None) -> Rules:
    allow = tuple(ln.strip() for ln in allowlist.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#"))
    data = json.loads(patterns.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise SystemExit("publish guard: unsupported forbidden-patterns version")
    content = dict(data["content_patterns"])
    if extra is not None:  # private literal names from the overlay; never committed
        for i, literal in enumerate(json.loads(extra.read_text(encoding="utf-8"))):
            content[f"overlay-literal-{i}"] = re.escape(literal)
    return Rules(
        allow=allow,
        path_globs={k: tuple(v) for k, v in data["path_globs"].items()},
        content={k: re.compile(v, re.IGNORECASE if k.startswith("overlay-") else 0) for k, v in content.items()},
    )


def _matches_any(path: str, globs: Iterable[str]) -> bool:
    return any(_glob_re(g).fullmatch(path) for g in globs)


def check(root: Path, paths: Sequence[str], rules: Rules) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if not _matches_any(path, rules.allow):
            findings.append(Finding(path, "outside-allowlist"))
        findings += [Finding(path, f"forbidden-path:{c}") for c, globs in rules.path_globs.items() if _matches_any(path, globs)]
        file = root / path
        if not file.is_file() or file.stat().st_size > MAX_SCAN_BYTES:
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = enumerate(text.splitlines(), 1)
        findings += [Finding(path, c, n) for n, line in lines for c, rx in rules.content.items() if rx.search(line)]
    return findings


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True)
    return [p for p in out.stdout.decode().split("\0") if p]


def self_test(rules: Rules) -> bool:
    """Canary: the guard must flag a seeded file of every content category."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "unlisted").mkdir()
        (root / "unlisted" / "canary.txt").write_text("\n".join(CANARY_LINES), encoding="utf-8")
        hit = {f.category for f in check(root, ["unlisted/canary.txt", "docs/x-CONFIDENTIAL.md"], rules)}
    want = set(rules.content) | {"outside-allowlist", "forbidden-path:confidential-doc"}
    return want <= hit


# Built at runtime so no token-shaped literal is ever committed.
CANARY_LINES = (
    "node." + "example" + ".ts.net",
    "100." + "100.1.1",
    "ghp" + "_" + "A" * 36,
    "xox" + "b-" + "0" * 12,
    "-----BEGIN " + "PRIVATE " + "KEY-----",
    "someone" + "@" + "gmail.com",
)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", choices=MODES)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--extra-patterns", type=Path, help="private JSON list of literals (from the overlay)")
    ap.add_argument("--list", action="store_true", help="list findings (paths and categories only)")
    ap.add_argument("--self-test", action="store_true", help="prove the guard fails on a seeded canary")
    args = ap.parse_args(argv)
    rules = load_rules(extra=args.extra_patterns)
    if args.self_test:
        ok = self_test(rules)
        print("publish guard self-test: " + ("PASS" if ok else "FAIL (guard missed a canary)"))
        return 0 if ok else 1
    mode = resolve_mode(args.mode)
    findings = check(args.root, tracked_files(args.root), rules)
    for category, n in sorted(Counter(f.category for f in findings).items()):
        print(f"{category}: {n}")
    if args.list:
        print("\n".join(f.render() for f in findings))
    print(f"publish guard ({mode}): {len(findings)} finding(s)")
    return 1 if findings and mode == "enforce" else 0


if __name__ == "__main__":
    sys.exit(main())
