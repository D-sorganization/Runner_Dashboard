"""Adapter interfaces for inbound pull/webhook and outbound SMTP."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from email_intake.models import InboundMessage


@dataclass(frozen=True)
class InboundFetchResult:
    """Messages fetched in one adapter poll cycle."""

    messages: tuple[InboundMessage, ...] = ()
    errors: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class InboundAdapter(ABC):
    """Pull or webhook-normalized source of :class:`InboundMessage`."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable adapter id (e.g. ``imap``, ``postmark-webhook``)."""

    @abstractmethod
    def fetch_new(self) -> InboundFetchResult:
        """Return newly available messages since the last successful fetch."""


class SmtpAdapter(ABC):
    """Outbound receipts/status (allowlisted recipients only)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable adapter id (e.g. ``smtp``)."""

    @abstractmethod
    def send_plain(self, *, to_address: str, subject: str, body: str) -> None:
        """Send a plain-text message; raises when disabled or recipient not allowed."""
