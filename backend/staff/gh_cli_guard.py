"""Pre-close evidence guard and PR body sanitizer for staff `gh` CLI calls."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence

EXEMPT_ISSUE_LABELS: frozenset[str] = frozenset(
    {
        "wontfix",
        "roadmap",
        "duplicate",
        "invalid",
        "not-planned",
        "verified",
        "verified-closed",
        "needs-human-review",
        "closure-guard-bypass",
        "keep-closed",
        "archived-backlog",
    }
)
_CLOSING_TMPL = r"(?:^|[^a-zA-Z0-9_])(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+(?:[\w.-]+/[\w.-]+)?#{num}\b"
_PR_REF_RE = re.compile(r"(?:\bPR\s*#|\bpull/|#)(\d+)\b", re.IGNORECASE)


def unescape_literal_newlines(text: str) -> str:
    """Replace literal `\\n` sequences passed by agents in `--body` strings."""
    return text.replace("\\r\\n", "\n").replace("\\n", "\n") if text and "\\n" in text else text


def _closing_re(issue_num: str | int) -> re.Pattern[str]:
    return re.compile(_CLOSING_TMPL.format(num=int(issue_num)), re.IGNORECASE)


def sanitize_pr_body_args(argv: Sequence[str], env: Mapping[str, str] | None = None) -> list[str]:
    """Unescape literal `\\n` in `gh pr create/edit --body` and ensure `Closes #<target>`."""
    env_map = os.environ if env is None else env
    args = list(argv)
    if len(args) < 2 or args[0] != "pr" or args[1] not in ("create", "edit"):
        return args
    t_kind = (env_map.get("STAFF_TARGET_KIND") or "").strip().lower()
    t_ref = (env_map.get("STAFF_TARGET_REF") or "").strip().lstrip("#")
    inject = args[1] == "create" and t_kind == "issue" and t_ref.isdigit()

    def _fix(raw: str) -> str:
        cleaned = unescape_literal_newlines(raw)
        if inject and not _closing_re(t_ref).search(cleaned):
            return f"{cleaned.rstrip()}\n\nCloses #{t_ref}\n" if cleaned.strip() else f"Closes #{t_ref}\n"
        return cleaned

    for idx, tok in enumerate(args):
        if tok in ("-b", "--body") and idx + 1 < len(args):
            args[idx + 1] = _fix(args[idx + 1])
        elif tok.startswith("--body="):
            args[idx] = "--body=" + _fix(tok.split("=", 1)[1])
    return args


def parse_issue_close_args(argv: Sequence[str]) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
    """Return `(issue_num, comment, reason, repo, args_without_comment)` for `gh issue close`."""
    if len(argv) < 2 or argv[0] != "issue" or argv[1] != "close":
        return None, None, None, None, list(argv)
    issue_num = comment = reason = repo = None
    stripped: list[str] = ["issue", "close"]
    i = 2
    while i < len(argv):
        tok = argv[i]
        if tok in ("-c", "--comment") and i + 1 < len(argv):
            comment, i = unescape_literal_newlines(argv[i + 1]), i + 2
        elif tok.startswith("--comment="):
            comment, i = unescape_literal_newlines(tok.split("=", 1)[1]), i + 1
        elif tok in ("-r", "--reason") and i + 1 < len(argv):
            reason = argv[i + 1]
            stripped.extend([tok, reason])
            i += 2
        elif tok.startswith("--reason="):
            reason = tok.split("=", 1)[1]
            stripped.append(tok)
            i += 1
        elif tok in ("-R", "--repo") and i + 1 < len(argv):
            repo = argv[i + 1]
            stripped.extend([tok, repo])
            i += 2
        elif tok.startswith("--repo="):
            repo = tok.split("=", 1)[1]
            stripped.append(tok)
            i += 1
        else:
            if not tok.startswith("-") and issue_num is None:
                m = re.search(r"(?:/issues/|^#?)(\d+)$", tok)
                issue_num = m.group(1) if m else tok
            stripped.append(tok)
            i += 1
    return issue_num, comment, reason, repo, stripped


def extract_pr_refs(text: str, exclude_issue: int | None = None) -> list[int]:
    """Extract unique PR/issue numbers `#NNN` or `/pull/NNN` from `text`."""
    seen: list[int] = []
    for match in _PR_REF_RE.finditer(unescape_literal_newlines(text or "")):
        num = int(match.group(1))
        if num > 0 and num != exclude_issue and num not in seen:
            seen.append(num)
    return seen


def _run_gh_json(real_gh: str, args: list[str]) -> object | None:
    try:
        proc = subprocess.run([real_gh, *args], capture_output=True, text=True, timeout=20, check=False)
        return json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else None
    except Exception:
        return None


def _label_names(items: object) -> set[str]:
    if not isinstance(items, list):
        return set()
    return {(str(x.get("name") or "") if isinstance(x, dict) else str(x)).lower() for x in items}


def _is_verified_merged_pr(real_gh: str, repo_args: list[str], pr_num: int) -> tuple[bool, str]:
    data = _run_gh_json(real_gh, ["pr", "view", str(pr_num), *repo_args, "--json", "mergedAt,body,labels"])
    if not isinstance(data, dict) or not data.get("mergedAt"):
        return False, ""
    return ("jules-verification-failed" not in _label_names(data.get("labels"))), str(data.get("body") or "")


def _backfill_pr_closing_keyword(real_gh: str, repo: str | None, pr_num: int, pr_body: str, issue_num: int) -> None:
    cleaned = unescape_literal_newlines(pr_body)
    if _closing_re(issue_num).search(cleaned) and "\\n" not in pr_body:
        return
    if not _closing_re(issue_num).search(cleaned):
        cleaned = f"{cleaned.rstrip()}\n\nCloses #{issue_num}\n" if cleaned.strip() else f"Closes #{issue_num}\n"
    endpoint = f"repos/{repo or '{owner}/{repo}'}/pulls/{pr_num}"
    try:
        subprocess.run(
            [real_gh, "api", "-X", "PATCH", endpoint, "-f", f"body={cleaned}", "--silent"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        pass


def check_and_prepare_issue_close(
    real_gh: str, argv: Sequence[str], env: Mapping[str, str] | None = None
) -> tuple[bool, list[str], str]:
    """Validate `gh issue close` has closure evidence and post `--comment` before closing."""
    env_map = os.environ if env is None else env
    if (env_map.get("GH_BYPASS_CLOSE_GUARD") or "").strip() == "1":
        return True, list(argv), ""
    issue_str, comment, reason, repo, stripped_args = parse_issue_close_args(argv)
    if not issue_str or not issue_str.isdigit():
        return True, list(argv), ""

    issue_num = int(issue_str)
    repo_args = ["-R", repo] if repo else []
    allowed = (reason or "").strip().lower() in ("not planned", "not_planned")
    matched_pr: tuple[int, str] | None = None

    def _check_text_refs(text: str) -> bool:
        nonlocal allowed, matched_pr
        for pr_num in extract_pr_refs(text, exclude_issue=issue_num):
            ok, pr_body = _is_verified_merged_pr(real_gh, repo_args, pr_num)
            if ok:
                allowed, matched_pr = True, (pr_num, pr_body)
                return True
        return False

    if not allowed and comment:
        _check_text_refs(comment)

    if not allowed:
        data = _run_gh_json(real_gh, ["issue", "view", str(issue_num), *repo_args, "--json", "labels,comments"])
        if isinstance(data, dict):
            if _label_names(data.get("labels")) & EXEMPT_ISSUE_LABELS:
                allowed = True
            for c in reversed(data.get("comments") or []) if not allowed else []:
                c_body = str(c.get("body") or "") if isinstance(c, dict) else ""
                if "Verify-Issue-Closure" not in c_body and _check_text_refs(c_body):
                    break

    if not allowed:
        cmd = [
            "pr",
            "list",
            *repo_args,
            "-s",
            "merged",
            "-S",
            f"{issue_num} in:body,title",
            "--json",
            "number,title,body,labels",
        ]
        prs = _run_gh_json(real_gh, cmd)
        pat = _closing_re(issue_num)
        for pr in prs if isinstance(prs, list) else []:
            if not isinstance(pr, dict) or "jules-verification-failed" in _label_names(pr.get("labels")):
                continue
            b = unescape_literal_newlines(f"{pr.get('title') or ''}\n{pr.get('body') or ''}")
            if pat.search(b):
                allowed, matched_pr = True, (int(pr["number"]), str(pr.get("body") or ""))
                break

    if not allowed:
        return (
            False,
            list(argv),
            (
                f"ERROR [staff-gh-close-guard]: Cannot close #{issue_num} without verified evidence.\n"
                f"Verify-Issue-Closure.yml will automatically reopen issues closed without proof.\n"
                f"Provide a merged PR citing `Closes #{issue_num}`, `--comment` citing PR #<num>, or an exempt label.\n"
            ),
        )

    if matched_pr is not None:
        _backfill_pr_closing_keyword(real_gh, repo, matched_pr[0], matched_pr[1], issue_num)
    if comment:
        subprocess.run(
            [real_gh, "issue", "comment", str(issue_num), *repo_args, "--body", comment],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return True, stripped_args, ""
    return True, list(argv), ""


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 0
    mode, real_gh, rest = args[0], args[1], args[2:]
    if mode == "sanitize-pr":
        sys.stdout.write(json.dumps(sanitize_pr_body_args(rest)))
        return 0
    if mode == "guard-close":
        ok, new_args, err = check_and_prepare_issue_close(real_gh, rest)
        if not ok:
            sys.stderr.write(err)
            return 2
        sys.stdout.write(json.dumps(new_args))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
