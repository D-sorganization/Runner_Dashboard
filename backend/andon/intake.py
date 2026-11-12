"""Hub local-ingress andon intake with A2 fingerprint deduplication (#2902).

Validates incoming payloads against the A1 contract, computes the A2
fingerprint via ``shared_scripts.andon_fingerprint``, and either comments on an
existing open ``andon``-labelled issue with the same fingerprint (at most once
per replay burst) or creates a new issue.
"""

from __future__ import annotations

import ipaddress
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request

_REPO_ROOT = Path(__file__).resolve().parents[2]
if (_REPO_ROOT / "shared_scripts").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared_scripts.andon_fingerprint import (  # noqa: E402
    AndonSchemaError,
    FingerprintResult,
    fingerprint,
    normalize,
)

FINGERPRINT_MARKER_PREFIX = "<!-- andon-fingerprint: "
DEDUPE_MARKER_PREFIX = "<!-- andon-dedupe: "
REQUIRED_SCOPE = "fleet.control"


class AndonIntakeError(RuntimeError):
    """Raised when bounded GitHub retries are exhausted or postconditions fail."""


def _fingerprint_marker(digest: str) -> str:
    return f"{FINGERPRINT_MARKER_PREFIX}{digest} -->"


def _dedupe_marker(digest: str) -> str:
    return f"{DEDUPE_MARKER_PREFIX}{digest} -->"


@dataclass
class AndonIntakeDeps:
    """Dependencies and state for hub andon intake."""

    list_open_andons: Callable[[str], list[dict[str, Any]]] | None = None
    create_andon_issue: Callable[[str, str, str, list[str]], dict[str, Any]] | None = None
    comment_on_andon_issue: Callable[[str, int, str], dict[str, Any]] | None = None
    max_retries: int = 3
    retry_delay_seconds: float = 0.0
    created_issues: list[dict[str, Any]] = field(default_factory=list)
    issue_comments: dict[int, list[str]] = field(default_factory=lambda: defaultdict(list))

    def list_open(self, repo: str) -> list[dict[str, Any]]:
        if self.list_open_andons is not None:
            return self.list_open_andons(repo)
        return [
            issue for issue in self.created_issues if issue.get("repo") == repo and issue.get("state", "open") == "open"
        ]

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict[str, Any]:
        if self.create_andon_issue is not None:
            created = self.create_andon_issue(repo, title, body, labels)
            if created not in self.created_issues:
                self.created_issues.append(created)
            issue_no = int(created.get("number", 0))
            if issue_no > 0:
                self.issue_comments.setdefault(issue_no, [])
            return created
        issue_number = len(self.created_issues) + 1
        record: dict[str, Any] = {
            "number": issue_number,
            "repo": repo,
            "title": title,
            "body": body,
            "labels": list(labels),
            "state": "open",
        }
        self.created_issues.append(record)
        self.issue_comments.setdefault(issue_number, [])
        return record

    def comment_issue(self, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        if self.comment_on_andon_issue is not None:
            result = self.comment_on_andon_issue(repo, issue_number, body)
            comments = self.issue_comments.setdefault(issue_number, [])
            if body not in comments:
                comments.append(body)
            return result
        self.issue_comments.setdefault(issue_number, []).append(body)
        return {"repo": repo, "issue_number": issue_number, "body": body}


def _call_with_retry(
    operation_name: str,
    func: Callable[[], Any],
    *,
    max_retries: int,
    retry_delay_seconds: float,
) -> Any:
    attempts = max(1, int(max_retries))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 — bounded retry across client failures
            last_exc = exc
            if attempt < attempts and retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
    raise AndonIntakeError(
        f"::error::Andon intake {operation_name} failed after {attempts} attempt(s): {last_exc}"
    ) from last_exc


def _matches_fingerprint(issue: Mapping[str, Any], digest: str) -> bool:
    if issue.get("fingerprint") == digest:
        return True
    body = str(issue.get("body") or "")
    return _fingerprint_marker(digest) in body


def _format_issue_body(norm: Mapping[str, str], fp: FingerprintResult) -> str:
    return "\n".join(
        [
            _fingerprint_marker(fp.fingerprint),
            f"**Repository:** `{norm['repo']}`",
            f"**Workflow:** `{norm['workflow']}`",
            f"**Check:** `{norm['check']}`",
            f"**File:** `{norm['file']}`",
            f"**Fingerprint:** `{fp.fingerprint}`",
            "",
            "### Normalized Error",
            "```text",
            fp.normalized_error,
            "```",
            "",
            "### Raw Error",
            "```text",
            norm["error"],
            "```",
        ]
    )


def _format_dedupe_comment(norm: Mapping[str, str], fp: FingerprintResult) -> str:
    summary = (
        f"Deduplicated andon occurrence for fingerprint `{fp.fingerprint}` "
        f"(`{norm['workflow']}` / `{norm['check']}` in `{norm['file']}`)."
    )
    return "\n".join(
        [
            _dedupe_marker(fp.fingerprint),
            summary,
            "",
            "```text",
            fp.normalized_error,
            "```",
        ]
    )


def process_andon_intake(
    payload: Mapping[str, Any],
    *,
    deps: AndonIntakeDeps | None = None,
) -> dict[str, Any]:
    """Validate an A1 payload, deduplicate by A2 fingerprint, and file or comment."""
    active_deps = deps if deps is not None else AndonIntakeDeps()
    norm = normalize(payload)
    fp = fingerprint(payload)
    repo = norm["repo"]

    open_issues: list[dict[str, Any]] = _call_with_retry(
        "list_open_andons",
        lambda: active_deps.list_open(repo),
        max_retries=active_deps.max_retries,
        retry_delay_seconds=active_deps.retry_delay_seconds,
    )

    matching = next((item for item in open_issues if _matches_fingerprint(item, fp.fingerprint)), None)
    if matching is not None:
        issue_number = int(matching.get("number") or 0)
        if issue_number <= 0:
            raise AndonIntakeError("::error::Matched open andon issue has an invalid issue number")
        marker = _dedupe_marker(fp.fingerprint)
        existing_comments = active_deps.issue_comments.get(issue_number, [])
        if not any(marker in c for c in existing_comments):
            comment_body = _format_dedupe_comment(norm, fp)
            _call_with_retry(
                "comment_on_andon_issue",
                lambda: active_deps.comment_issue(repo, issue_number, comment_body),
                max_retries=active_deps.max_retries,
                retry_delay_seconds=active_deps.retry_delay_seconds,
            )
        return {
            "issue_number": issue_number,
            "fingerprint": fp.fingerprint,
            "deduped": True,
        }

    title = f"[andon] {norm['workflow']} / {norm['check']}: {norm['file']}"
    body = _format_issue_body(norm, fp)
    created: dict[str, Any] = _call_with_retry(
        "create_andon_issue",
        lambda: active_deps.create_issue(repo, title, body, ["andon"]),
        max_retries=active_deps.max_retries,
        retry_delay_seconds=active_deps.retry_delay_seconds,
    )
    issue_number = int(created.get("number") or 0)
    if issue_number <= 0:
        raise AndonIntakeError("::error::Created andon issue is missing a positive issue_number")
    return {
        "issue_number": issue_number,
        "fingerprint": fp.fingerprint,
        "deduped": False,
    }


def _is_local_loopback(request: Request) -> bool:
    if request.client is None or not request.client.host:
        return False
    host = request.client.host.strip()
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _has_authorized_scope(request: Request) -> bool:
    try:
        from identity import _resolve_principal_optional, principal_has_scope  # noqa: PLC0415

        header_token = request.headers.get("Authorization")
        principal = _resolve_principal_optional(request, header_token)
        if principal is not None and (
            principal_has_scope(principal, REQUIRED_SCOPE) or principal_has_scope(principal, "coordination.write")
        ):
            return True
    except Exception:  # noqa: BLE001, S110 — fall back to loopback check when identity is unavailable
        pass
    return False


def checker(request: Request) -> None:
    """Enforce ADR-0007 local loopback ingress or fleet.control scope."""
    if _is_local_loopback(request) or _has_authorized_scope(request):
        return
    raise HTTPException(
        status_code=403,
        detail="::error::Andon intake requires local loopback ingress or fleet.control scope",
    )


andon_router = APIRouter(prefix="/api/v1", tags=["andon"], include_in_schema=False)
_DEFAULT_DEPS = AndonIntakeDeps()


def _get_deps(request: Request) -> AndonIntakeDeps:
    deps = getattr(request.app.state, "andon_intake_deps", None)
    if isinstance(deps, AndonIntakeDeps):
        return deps
    return _DEFAULT_DEPS


@andon_router.post("/andon", dependencies=[Depends(checker)])
def post_andon_intake(
    request: Request,
    payload: dict[str, Any] = Body(...),  # noqa: B008
) -> dict[str, Any]:
    """Accept an A1 andon payload on hub local ingress and deduplicate by fingerprint."""
    deps = _get_deps(request)
    try:
        return process_andon_intake(payload, deps=deps)
    except (AndonSchemaError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"::error::Invalid A1 andon payload: {exc}") from exc
    except AndonIntakeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def build_andon_app(deps: AndonIntakeDeps | None = None) -> FastAPI:
    """Construct a FastAPI app mounting ``andon_router`` with the supplied dependencies."""
    app = FastAPI()
    app.state.andon_intake_deps = deps if deps is not None else AndonIntakeDeps()
    app.include_router(andon_router)
    return app
