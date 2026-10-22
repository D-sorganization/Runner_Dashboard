"""Read-only, warn-only command-line report for the open PR cap."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence

from scripts.pr_cap_check import (
    PR_CAP,
    CapReport,
    cap_report,
    count_open,
    eligible_open_prs,
    render_summary,
    verdict,
    warn_report,
)
from shared_scripts.agent_identity import (
    normalize_pr_agent_id,
    pr_repository,
    resolve_pr_agent,
)

_RUNNER = Callable[..., subprocess.CompletedProcess[str]]
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class _ReportProblem(Exception):
    """Represent invalid or incomplete API data without exposing its payload."""


def prepare_report(
    repo: str,
    pr_number: int,
    cap: int = PR_CAP,
    *,
    run: _RUNNER = subprocess.run,
) -> str:
    """Fetch trusted REST metadata and return a deterministic summary."""
    return render_summary(prepare_report_data(repo, pr_number, cap, run=run))


def prepare_report_data(
    repo: str,
    pr_number: int,
    cap: int = PR_CAP,
    *,
    run: _RUNNER = subprocess.run,
) -> CapReport:
    """Fetch all open pages and the exact PR's current REST metadata.

    Pagination is a best-effort view, not an atomic GitHub snapshot. Duplicate
    or malformed rows fail open; the exact current PR response replaces any
    same-number row before counting.
    """
    if not _valid_repository(repo) or type(pr_number) is not int or pr_number <= 0:
        return warn_report(cap, "invalid_metadata")
    if type(cap) is not int or cap < 0:
        return warn_report(PR_CAP, "invalid_metadata")

    open_prs, error = fetch_open_prs(repo, run=run)
    if error is not None:
        return warn_report(cap, error)
    try:
        current_raw = _fetch_current(repo, pr_number, run)
        current_pr = _validate_pr(current_raw, repo, expected_number=pr_number)
        return _build_report(open_prs, current_pr, cap)
    except subprocess.TimeoutExpired:
        return warn_report(cap, "api_timeout")
    except _ApiRequestFailure:
        return warn_report(cap, "api_error")
    except OSError:
        return warn_report(cap, "api_error")
    except (_ReportProblem, json.JSONDecodeError, ValueError, TypeError):
        return warn_report(cap, "invalid_metadata")


def fetch_open_prs(
    repo: str, *, run: _RUNNER = subprocess.run
) -> tuple[list[Mapping[str, object]], str | None]:
    """Fetch and validate every open PR page for one target repository."""
    if not _valid_repository(repo):
        return [], "invalid_metadata"
    try:
        pages = _fetch_pages(repo, run)
        open_prs = [
            _validate_pr(item, repo, require_open=True)
            for page in pages
            for item in page
        ]
        _ensure_unique_numbers(open_prs)
        return open_prs, None
    except subprocess.TimeoutExpired:
        return [], "api_timeout"
    except (_ApiRequestFailure, OSError):
        return [], "api_error"
    except (_ReportProblem, json.JSONDecodeError, ValueError, TypeError):
        return [], "invalid_metadata"


def _fetch_pages(repo: str, run: _RUNNER) -> list[list[object]]:
    endpoint = f"repos/{repo}/pulls?state=open&per_page=100"
    response = _run_gh_api(
        ["gh", "api", "--paginate", "--slurp", "--method", "GET", endpoint], run
    )
    payload = _decode_response(response.stdout)
    if not isinstance(payload, list):
        raise _ReportProblem
    if not payload:
        return []
    if all(isinstance(page, list) for page in payload):
        return payload
    raise _ReportProblem


def _fetch_current(repo: str, pr_number: int, run: _RUNNER) -> object:
    endpoint = f"repos/{repo}/pulls/{pr_number}"
    response = _run_gh_api(["gh", "api", "--method", "GET", endpoint], run)
    return _decode_response(response.stdout)


def _run_gh_api(args: list[str], run: _RUNNER) -> subprocess.CompletedProcess[str]:
    response = run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if not isinstance(response, subprocess.CompletedProcess):
        raise _ReportProblem
    if response.returncode != 0:
        raise _ApiRequestFailure
    return response


class _ApiRequestFailure(Exception):
    """Represent a failed gh API call without retaining its stderr."""


def _decode_response(output: str) -> object:
    if not isinstance(output, str) or not output.strip():
        raise _ReportProblem
    return json.loads(output)


def _validate_pr(
    raw: object,
    repo: str,
    *,
    expected_number: int | None = None,
    require_open: bool = False,
) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise _ReportProblem
    number = raw.get("number")
    state = raw.get("state")
    draft = raw.get("draft")
    if type(number) is not int or number <= 0:
        raise _ReportProblem
    if expected_number is not None and number != expected_number:
        raise _ReportProblem
    if not isinstance(state, str) or state.casefold() not in {"open", "closed"}:
        raise _ReportProblem
    if require_open and state.casefold() != "open":
        raise _ReportProblem
    if type(draft) is not bool:
        raise _ReportProblem

    labels = raw.get("labels")
    user = raw.get("user")
    head = raw.get("head")
    base = raw.get("base")
    if not isinstance(labels, list) or not all(
        isinstance(label, Mapping) and isinstance(label.get("name"), str)
        for label in labels
    ):
        raise _ReportProblem
    if not isinstance(user, Mapping) or not all(
        isinstance(user.get(key), str) for key in ("login", "type")
    ):
        raise _ReportProblem
    if not isinstance(head, Mapping) or not isinstance(head.get("ref"), str):
        raise _ReportProblem
    if not isinstance(base, Mapping):
        raise _ReportProblem
    target_repo = base.get("repo")
    if not isinstance(target_repo, Mapping) or not isinstance(
        target_repo.get("full_name"), str
    ):
        raise _ReportProblem
    base_repository = target_repo["full_name"]
    if base_repository.casefold() != repo.casefold():
        raise _ReportProblem

    # Shared identity metadata accessors define which repository is the target.
    actual_repo = pr_repository(raw)
    if actual_repo is None or actual_repo.casefold() != repo.casefold():
        raise _ReportProblem
    return raw


def _ensure_unique_numbers(prs: Sequence[Mapping[str, object]]) -> None:
    numbers = [pr["number"] for pr in prs]
    if len(numbers) != len(set(numbers)):
        raise _ReportProblem


def _build_report(
    open_prs: Sequence[Mapping[str, object]],
    current_pr: Mapping[str, object],
    cap: int,
) -> CapReport:
    current_number = _number(current_pr)
    current_context = [pr for pr in open_prs if _number(pr) != current_number]
    if _state(current_pr) == "open":
        current_context.append(current_pr)
    eligible = eligible_open_prs(current_context)
    unknown = [
        pr
        for pr in eligible
        if (agent := resolve_pr_agent(pr)) is None or agent == "user"
    ]
    if _state(current_pr) != "open" or not eligible_open_prs([current_pr]):
        agent = resolve_pr_agent(current_pr)
        selected = _matching_prs(eligible, agent)
        return cap_report(
            status="PASS",
            reason="current_closed_draft_or_exempt",
            agent=agent,
            count=count_open(eligible, agent) if agent is not None else 0,
            cap=cap,
            matching_numbers=[_number(pr) for pr in selected],
            unknown_count=len(unknown),
            eligible_count=len(eligible),
        )

    agent = resolve_pr_agent(current_pr)
    if agent is None or agent == "user":
        return cap_report(
            status="WARN",
            reason="unknown_identity",
            agent=None,
            count=0,
            cap=cap,
            matching_numbers=[],
            unknown_count=len(unknown),
            eligible_count=len(eligible),
        )

    decision = verdict(current_context, current_pr, cap)
    matching = _matching_prs(eligible, agent)
    reason = (
        "cap_exceeded"
        if decision == "FAIL"
        else "policy_warn"
        if decision == "WARN"
        else "within_cap"
    )
    return cap_report(
        status="WARN" if decision in {"FAIL", "WARN"} else "PASS",
        reason=reason,
        agent=normalize_pr_agent_id(agent),
        count=count_open(eligible, agent),
        cap=cap,
        matching_numbers=[_number(pr) for pr in matching],
        unknown_count=len(unknown),
        eligible_count=len(eligible),
    )


def _matching_prs(
    prs: Sequence[Mapping[str, object]], agent: str | None
) -> list[Mapping[str, object]]:
    if agent is None or agent == "user":
        return []
    target = normalize_pr_agent_id(agent)
    return sorted(
        (pr for pr in prs if resolve_pr_agent(pr) == target),
        key=_number,
    )


def _number(pr: Mapping[str, object]) -> int:
    number = pr.get("number")
    if type(number) is not int or number <= 0:
        raise _ReportProblem
    return number


def _state(pr: Mapping[str, object]) -> str:
    state = pr.get("state")
    if not isinstance(state, str):
        raise _ReportProblem
    return state.casefold()


def _valid_repository(repo: str) -> bool:
    return bool(_REPOSITORY_RE.fullmatch(repo)) and all(
        part not in {".", ".."} for part in repo.split("/")
    )


def _repository_argument(value: str) -> str:
    if not _valid_repository(value):
        raise argparse.ArgumentTypeError("must be an owner/repository pair")
    return value


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def main(argv: Sequence[str] | None = None, *, run: _RUNNER = subprocess.run) -> int:
    """Print a report without ever returning a blocking status."""
    parser = argparse.ArgumentParser(description="Report the per-agent open PR cap.")
    parser.add_argument("--repo", required=True, type=_repository_argument)
    parser.add_argument("--pr", required=True, type=_positive_int)
    parser.add_argument("--cap", type=_non_negative_int, default=PR_CAP)
    parser.add_argument("--json", action="store_true", help="emit sanitized JSON")
    args = parser.parse_args(argv)
    report = prepare_report_data(args.repo, args.pr, args.cap, run=run)
    if args.json:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print(render_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
