"""Durable GitHub-backed persistence for Code Requests (CR-2, issue #1282).

Canonical storage is a GitHub issue in the target repository carrying
label ``code-request`` and ``code-request:<state>`` with a fenced YAML
front-matter block. Node-local ``code_requests.json`` serves as a cache
and can be completely rebuilt from GitHub issues at any time.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from code_requests.lifecycle import transition
from code_requests.model import (
    CodeRequest,
    CodeRequestAuditEvent,
    CodeRequestState,
    Requester,
    RequesterKind,
    code_request_from_issue,
    serialize_issue_body,
)
from config_schema import atomic_write_json
from dashboard_config import ORG
from dispatch.audit import CodeRequestTransitionAuditEntry, record_code_request_transition
from gh_utils import gh_api, gh_api_write
from projects.service import configured_repos

log = logging.getLogger("dashboard.code_requests.store")

_DEFAULT_CACHE_PATH = Path.home() / "actions-runners" / "dashboard" / "code_requests.json"

_AUDIT_COMMENT_PATTERN = re.compile(
    r"\*\*\[Code Request\]\*\*\s+State changed from\s+`(?P<from_state>[^`]+)`\s+to\s+"
    r"`(?P<to_state>[^`]+)`\s+by\s+`(?P<actor>[^`]+)`\.(?:\s+\(override\))?"
    r"(?:\n\n\*\*Reason:\*\*\s*(?P<reason>.*))?",
    re.DOTALL,
)


class CodeRequestStore:
    """GitHub-issue backed durable store with node-local JSON cache."""

    def __init__(
        self,
        cache_path: Path | None = None,
        org: str = ORG,
        fetch_fn: Callable[[str], Awaitable[Any]] | None = None,
        write_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.cache_path = cache_path or _DEFAULT_CACHE_PATH
        self.org = org
        self._fetch_fn = fetch_fn
        self._write_fn = write_fn
        self._lock = asyncio.Lock()

    async def _fetch(self, endpoint: str) -> Any:
        fn = self._fetch_fn or gh_api
        return await fn(endpoint)

    async def _write(self, endpoint: str, method: str = "POST", json_body: Any = None) -> Any:
        fn = self._write_fn or gh_api_write
        return await fn(endpoint, method=method, json_body=json_body)

    def _read_cache(self) -> list[dict[str, Any]]:
        if not self.cache_path.exists():
            return []
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return []

    def _write_cache(self, items: list[CodeRequest]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = [item.model_dump(mode="json") for item in items]
        atomic_write_json(self.cache_path, serialized)

    async def list(self, *, force_refresh: bool = False) -> list[CodeRequest]:
        """List all Code Requests, reading from local cache or rebuilding from GitHub."""
        async with self._lock:
            if not force_refresh and self.cache_path.exists():
                cached = self._read_cache()
                requests: list[CodeRequest] = []
                for entry in cached:
                    if not isinstance(entry, dict):
                        continue
                    try:
                        requests.append(CodeRequest.model_validate(entry))
                    except Exception:  # noqa: BLE001
                        req_id = str(entry.get("id") or "cr-legacy")
                        repo = str(entry.get("repository") or "Runner_Dashboard")
                        prompt = str(entry.get("prompt") or "")
                        state = CodeRequestState.DRAFT
                        if entry.get("status") == "dispatched":
                            state = CodeRequestState.EXECUTING
                        requests.append(
                            CodeRequest(
                                id=req_id,
                                repository=repo,
                                title=str(entry.get("title") or f"Code Request: {prompt[:40]}"),
                                state=state,
                                prompt=prompt,
                                requester=Requester(id="legacy", kind=RequesterKind.HUMAN),
                                branch=str(entry.get("branch") or "main"),
                                standards=list(entry.get("standards") or []),
                                created_at=str(entry.get("created_at") or datetime.now(UTC).isoformat()),
                                updated_at=str(entry.get("created_at") or datetime.now(UTC).isoformat()),
                            )
                        )
                if requests:
                    return requests

            return await self._rebuild_cache_locked()

    async def get(self, id_or_number: str) -> CodeRequest | None:
        """Find a Code Request by id, repo#number, or issue number."""
        items = await self.list()
        for item in items:
            if item.id == id_or_number:
                return item
            if item.issue_number is not None:
                if str(item.issue_number) == id_or_number:
                    return item
                if f"{item.repository}#{item.issue_number}" == id_or_number:
                    return item
        return None

    async def create(self, request: CodeRequest) -> CodeRequest:
        """Persist a new Code Request as a GitHub issue and update the cache."""
        async with self._lock:
            body = serialize_issue_body(request)
            title = request.title or f"Code Request: {request.prompt[:40]}"
            labels = ["code-request", f"code-request:{request.state.value}"]

            endpoint = f"/repos/{self.org}/{request.repository}/issues"
            payload = {
                "title": title,
                "body": body,
                "labels": labels,
            }

            resp = await self._write(endpoint, method="POST", json_body=payload)
            issue_number = resp.get("number")
            issue_url = resp.get("html_url")

            updated = request.model_copy(deep=True)
            updated.title = title
            if issue_number is not None:
                updated.issue_number = int(issue_number)
                if not updated.id or updated.id.startswith("cr-unknown"):
                    updated.id = f"cr-{request.repository}-{issue_number}"
            if issue_url:
                updated.issue_url = str(issue_url)

            # Update cache
            current = self._read_cache()
            current_models = [CodeRequest.model_validate(e) for e in current if isinstance(e, dict) and "id" in e]
            # Prepend or replace
            new_items = [updated] + [m for m in current_models if m.id != updated.id]
            self._write_cache(new_items)

            return updated

    async def save(self, request: CodeRequest) -> CodeRequest:
        """Update an existing Code Request on GitHub and in local cache."""
        async with self._lock:
            if request.issue_number is not None:
                body = serialize_issue_body(request)
                labels = ["code-request", f"code-request:{request.state.value}"]
                endpoint = f"/repos/{self.org}/{request.repository}/issues/{request.issue_number}"
                payload = {
                    "body": body,
                    "labels": labels,
                }
                await self._write(endpoint, method="PATCH", json_body=payload)

            current = self._read_cache()
            current_models = [CodeRequest.model_validate(e) for e in current if isinstance(e, dict) and "id" in e]
            updated_list = [request if m.id == request.id else m for m in current_models]
            if not any(m.id == request.id for m in current_models):
                updated_list.insert(0, request)
            self._write_cache(updated_list)

            return request

    async def transition(
        self,
        id_or_number: str,
        to_state: CodeRequestState | str,
        *,
        actor: str,
        reason: str,
        is_operator_override: bool = False,
        now: str | None = None,
    ) -> CodeRequest:
        """Execute a state transition, post an audit comment, and update records."""
        current = await self.get(id_or_number)
        if current is None:
            raise KeyError(f"Code Request {id_or_number!r} not found")

        updated = transition(
            current,
            to_state,
            actor=actor,
            reason=reason,
            is_operator_override=is_operator_override,
            now=now,
        )
        audit_event = updated.audit_trail[-1]

        # 1. Post audit comment to GitHub issue
        if current.issue_number is not None:
            comment_endpoint = f"/repos/{self.org}/{current.repository}/issues/{current.issue_number}/comments"
            override_str = " (override)" if is_operator_override else ""
            comment_body = (
                f"**[Code Request]** State changed from `{audit_event.from_state.value}` "
                f"to `{audit_event.to_state.value}` by `{actor}`.{override_str}\n\n"
                f"**Reason:** {reason.strip()}"
            )
            try:
                await self._write(comment_endpoint, method="POST", json_body={"body": comment_body})
            except Exception as err:  # noqa: BLE001
                log.warning("failed to post audit comment to GitHub: %s", err)

        # 2. Record to dispatch audit log
        record_code_request_transition(
            CodeRequestTransitionAuditEntry(
                actor=actor,
                from_state=audit_event.from_state.value,
                to_state=audit_event.to_state.value,
                reason=reason,
                timestamp=audit_event.timestamp,
                override=is_operator_override,
                request_id=updated.id,
                repository=updated.repository,
            )
        )

        # 3. Save issue body and labels and update cache
        return await self.save(updated)

    async def _fetch_comments_for_issue(self, repo: str, issue_number: int) -> builtins.list[CodeRequestAuditEvent]:
        """Fetch issue comments and extract Code Request audit events."""
        endpoint = f"/repos/{self.org}/{repo}/issues/{issue_number}/comments"
        try:
            comments = await self._fetch(endpoint)
            if not isinstance(comments, list):
                return []
        except Exception as err:  # noqa: BLE001
            log.warning("failed to fetch comments for %s#%s: %s", repo, issue_number, err)
            return []

        trail: builtins.list[CodeRequestAuditEvent] = []
        for c in comments:
            body = str(c.get("body") or "")
            match = _AUDIT_COMMENT_PATTERN.search(body)
            if match:
                f_state, t_state = match.group("from_state"), match.group("to_state")
                actor = match.group("actor")
                reason = match.group("reason") or ""
                override = "(override)" in body
                ts = str(c.get("created_at") or "")
                try:
                    trail.append(
                        CodeRequestAuditEvent(
                            actor=actor,
                            from_state=CodeRequestState(f_state),
                            to_state=CodeRequestState(t_state),
                            reason=reason.strip(),
                            timestamp=ts,
                            override=override,
                        )
                    )
                except ValueError:
                    pass
        return trail

    async def _rebuild_cache_locked(self) -> builtins.list[CodeRequest]:
        """Rebuild the local cache from GitHub issues across configured repositories."""
        repos = configured_repos()
        requests: builtins.list[CodeRequest] = []

        async def _fetch_repo_issues(repo: str) -> builtins.list[dict[str, Any]]:
            endpoint = f"/repos/{self.org}/{repo}/issues?labels=code-request&state=all&per_page=100"
            try:
                res = await self._fetch(endpoint)
                return res if isinstance(res, list) else []
            except Exception as err:  # noqa: BLE001
                log.warning("failed to fetch code-request issues from %s: %s", repo, err)
                return []

        repo_issues_lists = await asyncio.gather(*(_fetch_repo_issues(r) for r in repos))
        for repo, issues in zip(repos, repo_issues_lists, strict=False):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                # Skip pull requests returned by issues API
                if "pull_request" in issue:
                    continue
                issue_number = issue.get("number")
                audit_trail: builtins.list[CodeRequestAuditEvent] = []
                if issue_number is not None:
                    audit_trail = await self._fetch_comments_for_issue(repo, issue_number)
                req = code_request_from_issue(issue, default_repo=repo, audit_trail=audit_trail)
                requests.append(req)

        self._write_cache(requests)
        return requests
