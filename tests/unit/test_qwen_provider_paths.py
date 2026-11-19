"""Qwen CLI discovery on fleet staff unit PATH."""

from __future__ import annotations

from pathlib import Path

import pytest

from staff.qwen_provider import qwen_available


def test_qwen_available_finds_binary_under_local_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindir = tmp_path / ".local" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "qwen").write_text("#!/bin/sh\n", encoding="utf-8")
    (bindir / "qwen").chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.setenv("QWENCLOUD_TOKEN_PLAN_API_KEY", "sk-sp-test")  # pragma: allowlist secret
    assert qwen_available() is True