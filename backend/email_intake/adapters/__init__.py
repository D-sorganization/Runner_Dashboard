"""Inbound and outbound mail adapters."""

from email_intake.adapters.base import InboundAdapter, InboundFetchResult, SmtpAdapter

__all__ = ["InboundAdapter", "InboundFetchResult", "SmtpAdapter"]
