"""Telegram adapter for the rd-barb direct notify path (RD #3076): urgent questions/alerts only.

Owns Path B outbound delivery (Bot API ``sendMessage``) plus the rendering/filter/rate-limit shared
with Path A (the host-wired Claude Channels plugin); :func:`select_path` is the single source of truth
for which path owns inbound polling, so the two never both hold Telegram's one ``getUpdates`` consumer
slot. The bot token is read lazily from the env var named by ``RD_BARB_TELEGRAM_BOT_TOKEN_ENV`` (default
``TELEGRAM_BOT_TOKEN``) and never stored or logged; ``RD_BARB_TELEGRAM`` (default ``off``) kills both
paths. ``RD_BARB_TELEGRAM_CHAT_ID`` (required) and ``RD_BARB_TELEGRAM_MAX_PER_HOUR`` (default 6) are the
remaining config keys.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from staff.redaction import redact_sensitive_content

MAX_SUMMARY_CHARS = 200
DEFAULT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # noqa: S105 - the variable's name, not a secret  # pragma: allowlist secret
DEFAULT_MAX_PER_HOUR = 6
RATE_WINDOW_S = 3600.0
TELEGRAM_TIMEOUT_S = 10.0
URGENT_FILTER_KINDS = frozenset({"question", "alert"})

# Path A's required plugin posture (docs/rd-barb-telegram-setup.md): a Telegram reply must never approve a tool call.
CLAUDE_CHANNELS_TELEGRAM_CONFIG: Mapping[str, Any] = {"sender_allowlist": "allowlist", "permission_relay": False}

HttpPost = Callable[[str, dict[str, Any]], None]


def select_path(provider: str, *, channels_enabled: bool) -> str:
    """Which path owns delivery/inbound polling for ``provider`` ('plugin' or 'bot_api').

    Exactly one path is ever active: Path A (the Claude Channels plugin) only when the active
    provider is Claude *and* the plugin is actually configured; every other provider, or Claude
    without the plugin, falls back to Path B (the Bot API).
    """
    return "plugin" if provider == "claude" and channels_enabled else "bot_api"


def is_urgent(msg: Mapping[str, Any]) -> bool:
    """Only an urgent question or alert goes to Telegram; ``update``/non-urgent items never do."""
    return msg.get("kind") in URGENT_FILTER_KINDS and msg.get("importance", "normal") == "urgent"


def render_message(msg: Mapping[str, Any]) -> str:
    """Short plain text: summary, options (recommended marked), correlation id, expiry."""
    lines = [str(msg.get("summary") or "")[:MAX_SUMMARY_CHARS]]
    options = msg.get("options") or []
    for option in options:
        marker = " (Recommended)" if option.get("recommended") else ""
        lines.append(f"- {option['label']}{marker}")
    if action := msg.get("recommended_action"):
        lines.append(f"Recommended action: {action}")
    ref = msg.get("correlation_id") or msg.get("dedupe_key") or ""
    if ref:
        lines.append(f"Ref: {ref}")
    if msg.get("expiry"):
        lines.append(f"Expires: {msg['expiry']}")
    return redact_sensitive_content("\n".join(lines))


class RateLimiter:
    """At most ``max_per_hour`` urgent texts go out immediately; the rest fold into one digest."""

    def __init__(self, max_per_hour: int = DEFAULT_MAX_PER_HOUR) -> None:
        self._max = max(1, max_per_hour)
        self._sent_at: list[float] = []
        self._folded = 0

    def allow(self, now: float) -> bool:
        """True sends now and records the send; False folds this item into the pending digest."""
        self._sent_at = [t for t in self._sent_at if now - t < RATE_WINDOW_S]
        if len(self._sent_at) < self._max:
            self._sent_at.append(now)
            return True
        self._folded += 1
        return False

    def drain_digest(self) -> int:
        """The number of items folded since the last drain; resets the counter."""
        folded, self._folded = self._folded, 0
        return folded


def _bot_api_send(token: str, chat_id: str, text: str, *, http_post: HttpPost | None = None) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if http_post is not None:
        http_post(url, payload)
        return
    req = urllib.request.Request(  # noqa: S310 - fixed https host, never user-controlled
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=TELEGRAM_TIMEOUT_S) as resp:  # noqa: S310
        if resp.status >= 400:
            raise urllib.error.HTTPError(url, resp.status, "telegram rejected sendMessage", resp.headers, None)


class TelegramBotApiChannel:
    """Path B outbound: one ``sendMessage`` per non-rate-limited urgent question/alert."""

    name = "telegram"

    def __init__(
        self,
        cfg: Mapping[str, str],
        *,
        error_cls: type[Exception],
        environ: Mapping[str, str],
        http_post: HttpPost | None = None,
        rate_limiter: RateLimiter | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg, self._error, self._environ = dict(cfg), error_cls, environ
        self._http_post, self._clock = http_post, clock
        self._rate_limiter = rate_limiter or RateLimiter(
            int(cfg.get("RD_BARB_TELEGRAM_MAX_PER_HOUR") or DEFAULT_MAX_PER_HOUR)
        )

    def send(self, msg: dict[str, Any]) -> None:
        if (self._cfg.get("RD_BARB_TELEGRAM") or "off") != "on":
            raise self._error("telegram_disabled")
        if not is_urgent(msg):
            raise self._error("not_urgent")
        token, chat_id = self._settings()
        if not self._rate_limiter.allow(self._clock()):
            raise self._error("rate_limited_folded_into_digest")
        text = render_message(msg)
        try:
            _bot_api_send(token, chat_id, text, http_post=self._http_post)
        except TimeoutError:
            raise self._error("telegram_timeout") from None
        except urllib.error.HTTPError:
            raise self._error("telegram_rejected") from None
        except (urllib.error.URLError, OSError):
            raise self._error("telegram_connect_failed") from None

    def _settings(self) -> tuple[str, str]:
        chat_id = self._cfg.get("RD_BARB_TELEGRAM_CHAT_ID") or ""
        token = self._environ.get(self._cfg.get("RD_BARB_TELEGRAM_BOT_TOKEN_ENV") or DEFAULT_TOKEN_ENV, "")
        if not chat_id or not token:
            raise self._error("telegram_not_configured")
        return token, chat_id


def telegram_channel(
    cfg: Mapping[str, str],
    *,
    error_cls: type[Exception],
    environ: Mapping[str, str] | None = None,
    http_post: HttpPost | None = None,
    rate_limiter: RateLimiter | None = None,
) -> TelegramBotApiChannel:
    """Build the channel; ``environ`` defaults to ``os.environ`` (read lazily for the token only)."""
    if environ is None:
        import os  # noqa: PLC0415

        environ = os.environ
    return TelegramBotApiChannel(
        cfg, error_cls=error_cls, environ=environ, http_post=http_post, rate_limiter=rate_limiter
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``--dry-run`` (the only mode): render the envelope exactly as it would be sent, and send nothing."""
    import argparse  # noqa: PLC0415
    import json  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--json", required=True, help="envelope file, or - for stdin")
    parser.add_argument("--dry-run", action="store_true", required=True)
    args = parser.parse_args(argv)
    raw = sys.stdin.read() if args.json == "-" else Path(args.json).read_text(encoding="utf-8")
    msg = json.loads(raw)
    print(render_message(msg) if is_urgent(msg) else "not urgent: nothing would be sent")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() in tests
    import sys

    sys.exit(main())
