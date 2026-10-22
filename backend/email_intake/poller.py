"""Background IMAP polling loop."""

from __future__ import annotations

import asyncio
import logging

from email_intake.adapters.imap import ImapInboundAdapter
from email_intake.config import load_email_intake_config
from email_intake.pipeline import process_inbound_message

log = logging.getLogger("dashboard.email_intake.poller")


async def email_intake_poll_loop() -> None:
    """Poll IMAP when intake is enabled; no-op otherwise."""
    while True:
        cfg = load_email_intake_config()
        if not cfg.intake_enabled or not cfg.imap_configured():
            await asyncio.sleep(max(cfg.poll_interval_seconds, 30))
            continue
        adapter = ImapInboundAdapter(cfg)
        try:
            result = adapter.fetch_new()
            for msg in result.messages:
                await asyncio.to_thread(process_inbound_message, msg)
            for err in result.errors:
                log.info("email intake poll note: %s", err)
        except Exception as exc:  # noqa: BLE001
            log.warning("email intake poll failed: %s", exc.__class__.__name__)
        await asyncio.sleep(cfg.poll_interval_seconds)
