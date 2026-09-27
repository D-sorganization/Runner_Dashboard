#!/usr/bin/env python3
"""Operator smoke check for staff role chat threads (#1638).

Verifies that a staff role answers a thread end to end against a live dashboard
instance, ensuring reply completion, non-empty body, and prompt acknowledgement ordering.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def check_thread(messages: list[dict[str, Any]]) -> list[Check]:
    role_msg = next((m for m in messages if isinstance(m, dict) and m.get("author_kind") == "role"), None)
    user_msg = next((m for m in messages if isinstance(m, dict) and m.get("author_kind") == "user"), None)
    ack_msg = next(
        (
            m
            for m in messages
            if isinstance(m, dict)
            and m.get("author_kind") == "system"
            and isinstance(m.get("meta"), dict)
            and m["meta"].get("acknowledgement") is True
        ),
        None,
    )

    deliv = role_msg.get("delivery") if role_msg else None
    d_detail = (
        "delivery is 'complete'"
        if deliv == "complete"
        else (f"delivery is {deliv!r}" if role_msg else "missing role message")
    )
    checks = [Check("reply complete", deliv == "complete", d_detail)]

    body = role_msg.get("body_md") if role_msg else None
    stripped = body.strip() if isinstance(body, str) else ""
    b_detail = f"{len(stripped)} chars" if stripped else ("body is empty" if role_msg else "missing role message")
    checks.append(Check("reply non-empty", bool(stripped), b_detail))

    if user_msg is None:
        checks.append(Check("ack before reply", False, "missing user message"))
    elif ack_msg is None:
        checks.append(Check("ack before reply", False, "missing acknowledgement system message"))
    elif role_msg is None:
        checks.append(Check("ack before reply", False, "missing role message"))
    else:
        u_seq, a_seq, r_seq = user_msg.get("seq"), ack_msg.get("seq"), role_msg.get("seq")
        ok3 = isinstance(u_seq, int) and isinstance(a_seq, int) and isinstance(r_seq, int) and u_seq < a_seq < r_seq
        detail3 = f"user={u_seq}, ack={a_seq}, reply={r_seq}"
        checks.append(Check("ack before reply", ok3, detail3))

    return checks


Transport = Callable[[str, str, dict[str, Any] | None, dict[str, str]], tuple[int, dict[str, Any]]]


def urllib_transport(
    method: str,
    url: str,
    json_body: dict[str, Any] | None,
    headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            content = resp.read().decode("utf-8")
            parsed = json.loads(content) if content else {}
            status = getattr(resp, "status", getattr(resp, "code", 200))
            return int(status), parsed if isinstance(parsed, dict) else {}
    except urllib.error.HTTPError as exc:
        try:
            content = exc.read().decode("utf-8")
            parsed = json.loads(content) if content else {}
        except Exception:
            parsed = {}
        return int(exc.code), parsed if isinstance(parsed, dict) else {}


def run_smoke(
    base_url: str,
    role: str,
    question: str,
    timeout_s: float,
    *,
    transport: Transport = urllib_transport,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll_s: float = 5.0,
) -> tuple[list[Check], float]:
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ValueError(f"base_url must start with http:// or https://, got {base_url!r}")
    if not role or not role.strip():
        raise ValueError("role must be non-empty")
    if timeout_s <= 0:
        raise ValueError(f"timeout_s must be > 0, got {timeout_s}")

    base = base_url.rstrip("/")
    headers = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}
    token = os.environ.get("STAFF_SMOKE_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    status, create_data = transport(
        "POST", f"{base}/api/v1/staff/threads", {"title": "Staff chat smoke (#1638)", "role": role}, headers
    )
    if status != 201:
        raise RuntimeError(f"create thread failed with status {status}")
    thread_id = create_data.get("id")
    if not thread_id:
        raise RuntimeError(f"create thread response missing id: {create_data}")

    post_headers = {**headers, "Idempotency-Key": f"smoke-{uuid4().hex}"}
    status, _ = transport("POST", f"{base}/api/v1/staff/threads/{thread_id}/messages", {"body": question}, post_headers)
    if status != 202:
        raise RuntimeError(f"post message failed with status {status}")

    get_url = f"{base}/api/v1/staff/threads/{thread_id}?since_seq=0"
    start = clock()
    messages: list[dict[str, Any]] = []

    while True:
        status, get_data = transport("GET", get_url, None, headers)
        if status != 200:
            raise RuntimeError(f"get thread failed with status {status}")
        raw_msgs = get_data.get("messages")
        if isinstance(raw_msgs, list):
            messages = [m for m in raw_msgs if isinstance(m, dict)]

        role_msg = next((m for m in messages if m.get("author_kind") == "role"), None)
        delivery = role_msg.get("delivery") if role_msg else None
        if role_msg is not None and delivery not in ("pending", "streaming"):
            break

        now = clock()
        if now - start >= timeout_s:
            break
        sleep(poll_s)

    return check_thread(messages), clock() - start


def main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--base-url", required=True, help="Base URL of runner dashboard")
    parser.add_argument("--role", default="barb", help="Staff role to smoke test (default: barb)")
    parser.add_argument(
        "--question",
        default=(
            "Read-only check: in one sentence, what staff work is active right now? Do not propose or start any action."
        ),
        help="Question to post to thread",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="Polling timeout in seconds (default: 180)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    try:
        checks, elapsed = run_smoke(
            args.base_url, args.role, args.question, args.timeout, transport=transport or urllib_transport
        )
        all_ok = True
        for c in checks:
            print(f"{'PASS' if c.ok else 'FAIL'} {c.name}: {c.detail}")
            if not c.ok:
                all_ok = False
        print(f"elapsed {round(elapsed, 1):g}s")
        return 0 if all_ok else 1
    except (ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
