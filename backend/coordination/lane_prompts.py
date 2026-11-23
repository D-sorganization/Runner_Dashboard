"""Durable SQLite WAL store for exclusive lane-prompt checkouts."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from coordination.default_host_aliases import DEFAULT_HOST_ALIASES
from coordination.fleet_efficiency import enrich_lane_receipt

_DEFAULT_TTL_SEC = 3600
_BUSY_RETRIES = 8
_BUSY_SLEEP_S = 0.05

_PROVIDER_PREFIXES = (
    "codex",
    "claude",
    "agy",
    "cursor",
    "gemini",
    "deepseek",
    "qwen",
    "grok",
)

# Staff registry and lane catalog use different provider spellings; normalize to lane ids
# (short prefix form) before comparing checkout callers to seeded assignments.
_LANE_PROVIDER_ALIASES: dict[str, str] = {
    "cursor-agent": "cursor",
    "cursor_agent": "cursor",
    "deepseek-api": "deepseek",
    "deepseek_api": "deepseek",
    "antigravity": "agy",
    "gemini": "agy",
    "grok": "cursor",
    "grok-cli": "cursor",
    "grok_cli": "cursor",
    "qwen-code": "qwen",
    "qwen_cli": "qwen",
    "qwen-code-cli": "qwen",
    "codex-cli": "codex",
    "codex_cli": "codex",
    "claude-code": "claude",
    "claude-cli": "claude",
    "claude_code_cli": "claude",
}


def normalize_lane_provider(provider: str | None) -> str | None:
    """Map staff or registry provider ids onto lane checkout provider ids."""
    if provider is None:
        return None
    key = provider.strip().lower()
    if not key:
        return None
    return _LANE_PROVIDER_ALIASES.get(key, key)


# Hub-provisioned Cursor CLI lanes (#4565). ``release_sha`` / ``package_hash`` are
# placeholders until the hub publishes a fleet prompt package revision.
_DEFAULT_RELEASE_SHA = "bootstrap"
_DEFAULT_PACKAGE_HASH = "bootstrap"
_DEFAULT_CURSOR_MODEL = "composer-2.5"
_DEFAULT_GROK_CURSOR_MODEL = "grok-4.7-high"
_DEFAULT_QWEN_MODEL = "qwen3.8-flash"
_DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"

DEFAULT_LANE_ASSIGNMENTS: tuple[dict[str, Any], ...] = (
    {
        "lane_id": "codex-ct-1",
        "role": "backlog-completion",
        "repos": ["UpstreamDrift", "AffineDrift"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": "gpt-6-luna",
        "max_children": 3,
        "task_focus": "CT host Codex lane: UpstreamDrift and AffineDrift backlog completion.",
        "enabled": True,
    },
    {
        "lane_id": "cursor-ct-1",
        "role": "backlog-completion",
        "repos": ["Runner_Dashboard", "Repository_Management"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_CURSOR_MODEL,
        "max_children": 2,
        "task_focus": "CT host Cursor CLI lane (Composer 2.5).",
        "enabled": True,
    },
    {
        "lane_id": "cursor-oglaptop-1",
        "role": "backlog-completion",
        "repos": ["Runner_Dashboard", "Repository_Management"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_CURSOR_MODEL,
        "max_children": 2,
        "task_focus": "node-b host Cursor CLI lane (Composer 2.5).",
        "enabled": True,
    },
    {
        "lane_id": "cursor-ct-2",
        "role": "backlog-completion",
        "repos": ["Runner_Dashboard", "Repository_Management"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_GROK_CURSOR_MODEL,
        "max_children": 1,
        "task_focus": "CT host Cursor CLI lane (Grok 4.7 via cursor-agent).",
        "enabled": True,
    },
    {
        "lane_id": "cursor-oglaptop-2",
        "role": "backlog-completion",
        "repos": ["Runner_Dashboard", "Repository_Management"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_GROK_CURSOR_MODEL,
        "max_children": 1,
        "task_focus": "node-b host Cursor CLI lane (Grok 4.7 via cursor-agent).",
        "enabled": True,
    },
    {
        "lane_id": "qwen-ct-1",
        "role": "mad-scientist",
        "repos": ["Runner_Dashboard"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_QWEN_MODEL,
        "max_children": 1,
        "task_focus": "CT host Qwen Code CLI lane (draft-only qualification).",
        "enabled": True,
    },
    {
        "lane_id": "qwen-oglaptop-1",
        "role": "mad-scientist",
        "repos": ["Runner_Dashboard"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_QWEN_MODEL,
        "max_children": 1,
        "task_focus": "node-b host Qwen Code CLI lane (draft-only qualification).",
        "enabled": True,
    },
    {
        "lane_id": "deepseek-ct-1",
        "role": "mad-scientist",
        "repos": ["Runner_Dashboard"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_DEEPSEEK_MODEL,
        "max_children": 1,
        "task_focus": "CT host DeepSeek API CLI lane (draft-only qualification).",
        "enabled": True,
    },
    {
        "lane_id": "deepseek-oglaptop-1",
        "role": "mad-scientist",
        "repos": ["Runner_Dashboard"],
        "release_sha": _DEFAULT_RELEASE_SHA,
        "package_hash": _DEFAULT_PACKAGE_HASH,
        "subagent_model": _DEFAULT_DEEPSEEK_MODEL,
        "max_children": 1,
        "task_focus": "node-b host DeepSeek API CLI lane (draft-only qualification).",
        "enabled": True,
    },
)


def _load_host_aliases() -> dict[str, str]:
    # Built-in defaults live in default_host_aliases (the one allowlisted module);
    # host_aliases.json and RUNNER_DASHBOARD_HOST_ALIASES override them.
    aliases: dict[str, str] = dict(DEFAULT_HOST_ALIASES)
    cfg_dir = Path(
        os.environ.get(
            "RUNNER_DASHBOARD_CONFIG_DIR",
            str(Path("~/.config/runner-dashboard").expanduser()),
        )
    )
    alias_file = cfg_dir / "host_aliases.json"
    if alias_file.is_file():
        try:
            raw = json.loads(alias_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        aliases[k.strip().lower()] = v.strip().lower()
        except (OSError, ValueError):
            pass
    env_raw = os.environ.get("RUNNER_DASHBOARD_HOST_ALIASES", "").strip()
    if env_raw:
        try:
            raw = json.loads(env_raw)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        aliases[k.strip().lower()] = v.strip().lower()
        except ValueError:
            pass
    return aliases


def _infer_provider(session_id: str, explicit_provider: str | None, principal: str | None = None) -> str | None:
    raw = (explicit_provider or "").strip().lower()
    if raw:
        return normalize_lane_provider(raw)
    sid = session_id.strip().lower()
    for prefix in _PROVIDER_PREFIXES:
        if sid.startswith(f"{prefix}-") or sid == prefix:
            inferred = "agy" if prefix == "gemini" else prefix
            return normalize_lane_provider(inferred)
    prin = (principal or "").strip().lower()
    if prin.startswith("agent-"):
        tail = prin[len("agent-") :]
        for prefix in _PROVIDER_PREFIXES:
            if tail == prefix or tail.startswith(f"{prefix}-"):
                inferred = "agy" if prefix == "gemini" else prefix
                return normalize_lane_provider(inferred)
    return None


def _normalize_host(host: str | None, aliases: dict[str, str] | None = None) -> str | None:
    if not host:
        return None
    key = host.strip().lower()
    alias_map = _load_host_aliases() if aliases is None else aliases
    return alias_map.get(key, key)


def _strip_session_suffix_tokens(tokens: list[str]) -> list[str]:
    out = list(tokens)
    while out and (out[-1].isdigit() or (len(out[-1]) >= 6 and all(c in "0123456789abcdef" for c in out[-1]))):
        out.pop()
    return out


def _infer_host(session_id: str, explicit_host: str | None) -> str | None:
    aliases = _load_host_aliases()
    norm = _normalize_host(explicit_host, aliases)
    if norm is not None:
        return norm
    tokens = session_id.strip().lower().split("-")
    for tok in tokens:
        if tok in aliases:
            return aliases[tok]
    if tokens and tokens[0] in _PROVIDER_PREFIXES:
        body = _strip_session_suffix_tokens(tokens[1:])
        if body and body[0] not in ("owner", "session", "sess", "worker", "test"):
            candidate = "-".join(body)
            return aliases.get(candidate, candidate)
    return None


def _is_lane_compatible(cand: sqlite3.Row, want_provider: str | None, want_host: str | None) -> bool:
    cand_prov = normalize_lane_provider(_lane_provider(cand["lane_id"], cand["subagent_model"]))
    want_norm = normalize_lane_provider(want_provider)
    if want_norm is not None and cand_prov is not None and cand_prov != want_norm:
        return False
    cand_host = _lane_host(cand["lane_id"])
    if cand_host is not None and (want_host is None or cand_host != want_host):
        return False
    return True


def _pick_want_provider_for_candidates(
    candidates: list[sqlite3.Row],
    inferred_provider: str | None,
    provider_down: set[str],
    want_host: str | None,
) -> str | None:
    """Honor inferred provider unless marked down; then walk the lane chain for an eligible lane."""
    from coordination import provider_down_markers  # noqa: PLC0415

    want_norm = normalize_lane_provider(inferred_provider)
    if not provider_down or (want_norm is not None and want_norm not in provider_down):
        return want_norm

    chain = list(provider_down_markers.LANE_CHECKOUT_PROVIDER_CHAIN)
    if want_norm and want_norm in chain:
        idx = chain.index(want_norm)
        order = chain[idx:] + chain[:idx]
    elif want_norm:
        order = [want_norm, *[p for p in chain if p != want_norm]]
    else:
        order = chain

    for prov in order:
        if prov in provider_down:
            continue
        for cand in candidates:
            if _is_lane_compatible(cand, prov, want_host):
                return prov
    return None


def _lane_provider(lane_id: str, subagent_model: str) -> str | None:
    lid = lane_id.strip().lower()
    for prefix in _PROVIDER_PREFIXES:
        if lid.startswith(f"{prefix}-"):
            if prefix == "gemini":
                return "agy"
            if prefix == "deepseek":
                return "deepseek"
            return prefix
    model = (subagent_model or "").strip().lower()
    if model.startswith("claude-"):
        return "claude"
    if model.startswith("gemini-"):
        return "agy"
    if model.startswith("gpt-"):
        return "codex"
    if model.startswith("deepseek-"):
        return "deepseek"
    if model.startswith("qwen"):
        return "qwen"
    if model.startswith("composer-") or model.startswith("grok-"):
        return "cursor"
    return None


def _lane_host(lane_id: str) -> str | None:
    aliases = _load_host_aliases()
    tokens = lane_id.strip().lower().split("-")
    if not tokens or tokens[0] not in _PROVIDER_PREFIXES:
        for tok in tokens[1:]:
            if tok in aliases:
                return aliases[tok]
        return None
    body = tokens[1:-1] if len(tokens) >= 3 and (tokens[-1].isdigit() or tokens[-1] == "test") else tokens[1:]
    if not body or body[0] in ("owner", "session", "sess", "worker"):
        return None
    candidate = "-".join(body)
    return aliases.get(candidate, candidate)


class LaneConflictError(RuntimeError):
    """Raised when a lane is already actively checked out or none are eligible."""


class StaleLaneFenceError(RuntimeError):
    """Raised when a heartbeat/release presents a stale fence or wrong session."""


def _default_db_path() -> Path:
    base = Path(
        os.environ.get(
            "RUNNER_DASHBOARD_CONFIG_DIR",
            str(Path("~/.config/runner-dashboard").expanduser()),
        )
    )
    return base / "lane_prompts.sqlite3"


class LanePromptStore:
    """Thread-safe, durable SQLite WAL store for lane-prompt checkouts."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _run_tx(self, fn: Any) -> Any:
        with self._lock:
            for attempt in range(_BUSY_RETRIES):
                conn = self._connect()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    result = fn(conn)
                    if conn.in_transaction:
                        conn.execute("COMMIT")
                    return result
                except sqlite3.OperationalError as exc:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    if "locked" in str(exc).lower() and attempt < _BUSY_RETRIES - 1:
                        time.sleep(_BUSY_SLEEP_S * (attempt + 1))
                        continue
                    raise
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
                finally:
                    conn.close()

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS lane_assignments (
                    lane_id TEXT PRIMARY KEY,
                    role TEXT NOT NULL,
                    repos_json TEXT NOT NULL,
                    release_sha TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    subagent_model TEXT NOT NULL,
                    max_children INTEGER NOT NULL,
                    task_focus TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lane_checkouts (
                    checkout_id TEXT PRIMARY KEY,
                    lane_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    fence_token INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    ttl_sec INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    heartbeat_at REAL NOT NULL,
                    progress_at REAL,
                    release_sha TEXT NOT NULL,
                    package_hash TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_lane
                    ON lane_checkouts(lane_id) WHERE status = 'active';
                """)
        finally:
            conn.close()

    def seed_defaults(self) -> None:
        """Register :data:`DEFAULT_LANE_ASSIGNMENTS` (idempotent upsert per lane_id)."""
        for row in DEFAULT_LANE_ASSIGNMENTS:
            self.register_assignment(**row)

    def register_assignment(
        self,
        *,
        lane_id: str,
        role: str,
        repos: list[str],
        release_sha: str,
        package_hash: str,
        subagent_model: str,
        max_children: int,
        task_focus: str = "",
        enabled: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                """
                INSERT INTO lane_assignments (
                    lane_id, role, repos_json, release_sha, package_hash,
                    subagent_model, max_children, task_focus, enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lane_id) DO UPDATE SET
                    role=excluded.role,
                    repos_json=excluded.repos_json,
                    release_sha=excluded.release_sha,
                    package_hash=excluded.package_hash,
                    subagent_model=excluded.subagent_model,
                    max_children=excluded.max_children,
                    task_focus=excluded.task_focus,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (
                    lane_id,
                    role,
                    json.dumps(repos),
                    release_sha,
                    package_hash,
                    subagent_model,
                    int(max_children),
                    task_focus,
                    1 if enabled else 0,
                    ts,
                ),
            )
            return {"lane_id": lane_id, "role": role, "release_sha": release_sha}

        return self._run_tx(_tx)

    def list_catalog(self, now: float | None = None) -> list[dict[str, Any]]:
        ts = time.time() if now is None else float(now)

        def _tx(conn: sqlite3.Connection) -> list[dict[str, Any]]:
            conn.execute(
                "UPDATE lane_checkouts SET status='expired' WHERE status='active' AND expires_at <= ?",
                (ts,),
            )
            rows = conn.execute("""
                SELECT a.lane_id, a.role, a.repos_json, a.release_sha, a.package_hash,
                       a.subagent_model, a.max_children, a.task_focus, a.enabled,
                       c.checkout_id, c.session_id, c.fence_token, c.expires_at
                FROM lane_assignments a
                LEFT JOIN lane_checkouts c
                  ON c.lane_id = a.lane_id AND c.status = 'active'
                ORDER BY a.lane_id
                """).fetchall()
            return [
                {
                    "lane_id": r["lane_id"],
                    "role": r["role"],
                    "repos": json.loads(r["repos_json"]),
                    "release_sha": r["release_sha"],
                    "package_hash": r["package_hash"],
                    "subagent_model": r["subagent_model"],
                    "max_children": r["max_children"],
                    "task_focus": r["task_focus"],
                    "enabled": bool(r["enabled"]),
                    "active_checkout_id": r["checkout_id"],
                    "active_session_id": r["session_id"],
                }
                for r in rows
            ]

        return self._run_tx(_tx)

    def checkout(
        self,
        *,
        session_id: str,
        principal: str,
        idempotency_key: str,
        lane_id: str | None = None,
        role: str | None = None,
        provider: str | None = None,
        host: str | None = None,
        ttl_sec: int = _DEFAULT_TTL_SEC,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)
        ttl = max(60, int(ttl_sec))
        from coordination import provider_down_markers  # noqa: PLC0415

        inferred_provider = _infer_provider(session_id, provider, principal)
        provider_down = provider_down_markers.providers_marked_down(now=ts)
        want_provider = normalize_lane_provider(inferred_provider)
        want_host = _infer_host(session_id, host)
        # Idempotency keys are caller supplied, so scope them to the session and
        # principal to prevent replaying another caller's receipt and fence.
        scoped_idempotency_key = json.dumps(
            [principal, session_id, idempotency_key],
            ensure_ascii=False,
            separators=(",", ":"),
        )

        def _check_receipt_compatible(conn: sqlite3.Connection, receipt_json: str) -> dict[str, Any]:
            rec = json.loads(receipt_json)
            row = conn.execute(
                "SELECT * FROM lane_assignments WHERE lane_id = ? AND enabled = 1",
                (rec["lane_id"],),
            ).fetchone()
            if row is None or not _is_lane_compatible(row, want_provider, want_host):
                raise LaneConflictError(f"Lane {rec['lane_id']!r} is incompatible with caller provider/host")
            return rec

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            effective_want_provider = want_provider
            existing_idem = conn.execute(
                """SELECT receipt_json FROM lane_checkouts
                   WHERE idempotency_key = ? AND principal = ? AND session_id = ?""",
                (scoped_idempotency_key, principal, session_id),
            ).fetchone()
            if existing_idem is not None:
                return _check_receipt_compatible(conn, existing_idem["receipt_json"])

            # Honor pre-scope database records only for the original owner.
            legacy_idem = conn.execute(
                """SELECT receipt_json FROM lane_checkouts
                   WHERE idempotency_key = ? AND principal = ? AND session_id = ?""",
                (idempotency_key, principal, session_id),
            ).fetchone()
            if legacy_idem is not None:
                return _check_receipt_compatible(conn, legacy_idem["receipt_json"])

            conn.execute(
                "UPDATE lane_checkouts SET status='expired' WHERE status='active' AND expires_at <= ?",
                (ts,),
            )

            if lane_id is None and role is None:
                active_self = conn.execute(
                    """SELECT c.receipt_json, a.lane_id, a.subagent_model
                       FROM lane_checkouts c
                       JOIN lane_assignments a ON a.lane_id = c.lane_id
                       WHERE c.session_id = ? AND c.status = 'active'""",
                    (session_id,),
                ).fetchone()
                if active_self is not None and _is_lane_compatible(active_self, effective_want_provider, want_host):
                    return enrich_lane_receipt(json.loads(active_self["receipt_json"]))

            if lane_id is not None:
                row = conn.execute(
                    "SELECT * FROM lane_assignments WHERE lane_id = ? AND enabled = 1",
                    (lane_id,),
                ).fetchone()
                if row is None:
                    raise LaneConflictError(f"Lane {lane_id!r} not found or disabled")
                lane_prov = normalize_lane_provider(_lane_provider(row["lane_id"], row["subagent_model"]))
                if provider_down and lane_prov is not None and lane_prov in provider_down:
                    raise LaneConflictError(f"Lane {lane_id!r} provider {lane_prov!r} is marked down")
                if not _is_lane_compatible(row, effective_want_provider, want_host):
                    raise LaneConflictError(f"Lane {lane_id!r} is incompatible with caller provider/host")
                held = conn.execute(
                    "SELECT session_id FROM lane_checkouts WHERE lane_id = ? AND status = 'active'",
                    (lane_id,),
                ).fetchone()
                if held is not None:
                    if held["session_id"] == session_id:
                        rec = conn.execute(
                            "SELECT receipt_json FROM lane_checkouts WHERE lane_id = ? AND status = 'active'",
                            (lane_id,),
                        ).fetchone()
                        assert rec is not None
                        return enrich_lane_receipt(json.loads(rec["receipt_json"]))
                    raise LaneConflictError(f"Lane {lane_id!r} already held by {held['session_id']}")
                target = row
            else:
                query = """
                    SELECT a.* FROM lane_assignments a
                    LEFT JOIN lane_checkouts c
                      ON c.lane_id = a.lane_id AND c.status = 'active'
                    WHERE a.enabled = 1 AND c.checkout_id IS NULL
                """
                params: list[Any] = []
                if role is not None:
                    query += " AND a.role = ?"
                    params.append(role)
                query += " ORDER BY a.lane_id"
                candidates = conn.execute(query, params).fetchall()
                effective_want_provider = _pick_want_provider_for_candidates(
                    candidates,
                    inferred_provider,
                    provider_down,
                    want_host,
                )
                filtered = [
                    cand
                    for cand in candidates
                    if _is_lane_compatible(cand, effective_want_provider, want_host)
                ]
                if provider_down:
                    filtered = [
                        cand
                        for cand in filtered
                        if normalize_lane_provider(_lane_provider(cand["lane_id"], cand["subagent_model"]))
                        not in provider_down
                    ]

                def _provider_chain_rank(row: sqlite3.Row) -> int:
                    prov = normalize_lane_provider(_lane_provider(row["lane_id"], row["subagent_model"]))
                    chain = provider_down_markers.LANE_CHECKOUT_PROVIDER_CHAIN
                    if prov in chain:
                        return chain.index(prov)
                    return len(chain)

                filtered.sort(
                    key=lambda r: (
                        0 if want_host is not None and _lane_host(r["lane_id"]) == want_host else 1,
                        _provider_chain_rank(r),
                        r["lane_id"],
                    )
                )
                if not filtered:
                    raise LaneConflictError("No eligible unclaimed lane available")
                target = filtered[0]

            fence_row = conn.execute(
                "SELECT COALESCE(MAX(fence_token), 0) + 1 AS next_fence FROM lane_checkouts WHERE lane_id = ?",
                (target["lane_id"],),
            ).fetchone()
            fence_token = int(fence_row["next_fence"])
            checkout_id = f"lchk-{uuid.uuid4().hex[:12]}"
            expires_at = ts + ttl
            receipt = {
                "checkout_id": checkout_id,
                "lane_id": target["lane_id"],
                "role": target["role"],
                "repos": json.loads(target["repos_json"]),
                "principal": principal,
                "session_id": session_id,
                "idempotency_key": idempotency_key,
                "fence_token": fence_token,
                "status": "active",
                "ttl_sec": ttl,
                "expires_at": expires_at,
                "heartbeat_at": ts,
                "progress_at": None,
                "release_sha": target["release_sha"],
                "package_hash": target["package_hash"],
                "subagent_model": target["subagent_model"],
                "max_children": target["max_children"],
                "task_focus": target["task_focus"],
            }
            conn.execute(
                """
                INSERT INTO lane_checkouts (
                    checkout_id, lane_id, role, session_id, principal, idempotency_key,
                    fence_token, status, ttl_sec, expires_at, heartbeat_at, progress_at,
                    release_sha, package_hash, receipt_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    checkout_id,
                    target["lane_id"],
                    target["role"],
                    session_id,
                    principal,
                    scoped_idempotency_key,
                    fence_token,
                    ttl,
                    expires_at,
                    ts,
                    target["release_sha"],
                    target["package_hash"],
                    json.dumps(receipt),
                    ts,
                ),
            )
            return enrich_lane_receipt(receipt)

        return self._run_tx(_tx)

    def heartbeat(
        self,
        *,
        checkout_id: str,
        fence_token: int,
        session_id: str,
        ttl_sec: int = _DEFAULT_TTL_SEC,
        progress: bool = False,
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)
        ttl = max(60, int(ttl_sec))

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT * FROM lane_checkouts WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "active"
                or int(row["fence_token"]) != int(fence_token)
                or row["session_id"] != session_id
            ):
                raise StaleLaneFenceError(f"Stale or invalid checkout {checkout_id!r}")
            new_exp = ts + ttl
            new_prog = ts if progress else row["progress_at"]
            receipt = json.loads(row["receipt_json"])
            receipt["expires_at"] = new_exp
            receipt["heartbeat_at"] = ts
            receipt["progress_at"] = new_prog
            conn.execute(
                """
                UPDATE lane_checkouts
                SET expires_at = ?, heartbeat_at = ?, progress_at = ?, receipt_json = ?
                WHERE checkout_id = ?
                """,
                (new_exp, ts, new_prog, json.dumps(receipt), checkout_id),
            )
            return enrich_lane_receipt(receipt)

        return self._run_tx(_tx)

    def release(
        self,
        *,
        checkout_id: str,
        fence_token: int,
        session_id: str,
        reason: str = "completed",
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = time.time() if now is None else float(now)

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            row = conn.execute(
                "SELECT * FROM lane_checkouts WHERE checkout_id = ?",
                (checkout_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "active"
                or int(row["fence_token"]) != int(fence_token)
                or row["session_id"] != session_id
            ):
                raise StaleLaneFenceError(f"Stale or invalid checkout {checkout_id!r}")
            receipt = json.loads(row["receipt_json"])
            receipt["status"] = "released"
            receipt["released_at"] = ts
            receipt["release_reason"] = reason
            conn.execute(
                "UPDATE lane_checkouts SET status = 'released', receipt_json = ? WHERE checkout_id = ?",
                (json.dumps(receipt), checkout_id),
            )
            return receipt

        return self._run_tx(_tx)

    def health_summary(
        self,
        *,
        stall_heartbeat_sec: int = 900,
        stall_progress_sec: int = 1800,
        expired_window_sec: int = 86400,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Classify active, stalled, unresolved-expired, and unassigned idle lanes."""
        ts = time.time() if now is None else float(now)
        hb_thresh = max(60, int(stall_heartbeat_sec))
        prog_thresh = max(60, int(stall_progress_sec))
        exp_window = max(60, int(expired_window_sec))

        def _tx(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                "UPDATE lane_checkouts SET status='expired' WHERE status='active' AND expires_at <= ?",
                (ts,),
            )
            assignments = conn.execute(
                "SELECT lane_id, role, task_focus, enabled FROM lane_assignments ORDER BY lane_id"
            ).fetchall()
            enabled_lanes = {a["lane_id"] for a in assignments if a["enabled"]}
            active_rows = conn.execute("SELECT * FROM lane_checkouts WHERE status='active' ORDER BY lane_id").fetchall()
            expired_rows = conn.execute(
                """
                SELECT c.lane_id, c.checkout_id, c.session_id, c.expires_at
                FROM lane_checkouts c
                JOIN lane_assignments a ON a.lane_id = c.lane_id
                WHERE c.status = 'expired'
                  AND a.enabled = 1
                  AND (? - c.expires_at) <= ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM lane_checkouts c2
                      WHERE c2.lane_id = c.lane_id
                        AND c2.status IN ('active', 'released')
                        AND c2.created_at > c.expires_at
                  )
                ORDER BY c.expires_at DESC
                LIMIT 25
                """,
                (ts, exp_window),
            ).fetchall()

            active_lane_ids: set[str] = set()
            healthy_active: list[dict[str, Any]] = []
            stalled_heartbeat: list[dict[str, Any]] = []
            stalled_progress: list[dict[str, Any]] = []

            for r in active_rows:
                if r["lane_id"] not in enabled_lanes:
                    continue
                active_lane_ids.add(r["lane_id"])
                hb_age = ts - float(r["heartbeat_at"])
                prog_ref = float(r["progress_at"]) if r["progress_at"] is not None else float(r["created_at"])
                prog_age = ts - prog_ref
                item = {
                    "lane_id": r["lane_id"],
                    "checkout_id": r["checkout_id"],
                    "session_id": r["session_id"],
                    "heartbeat_age_sec": round(hb_age, 1),
                    "progress_age_sec": round(prog_age, 1),
                    "expires_in_sec": round(float(r["expires_at"]) - ts, 1),
                }
                if hb_age >= hb_thresh:
                    stalled_heartbeat.append(item)
                elif prog_age >= prog_thresh:
                    stalled_progress.append(item)
                else:
                    healthy_active.append(item)

            idle_lanes = [
                {
                    "lane_id": a["lane_id"],
                    "role": a["role"],
                    "task_focus": a["task_focus"],
                }
                for a in assignments
                if a["enabled"] and a["lane_id"] not in active_lane_ids
            ]
            expired_recent = [
                {
                    "lane_id": r["lane_id"],
                    "checkout_id": r["checkout_id"],
                    "session_id": r["session_id"],
                    "expired_ago_sec": round(ts - float(r["expires_at"]), 1),
                }
                for r in expired_rows
            ]
            return {
                "checked_at": ts,
                "healthy_active": healthy_active,
                "stalled_heartbeat": stalled_heartbeat,
                "stalled_progress": stalled_progress,
                "expired_recent": expired_recent,
                "idle_lanes": idle_lanes,
                "needs_attention": bool(stalled_heartbeat or stalled_progress or expired_recent or idle_lanes),
            }

        return self._run_tx(_tx)


_default_store_lock = threading.Lock()
_default_store: LanePromptStore | None = None


def get_lane_prompt_store() -> LanePromptStore:
    """Return the process-wide LanePromptStore singleton."""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = LanePromptStore()
            _default_store.seed_defaults()
        return _default_store
