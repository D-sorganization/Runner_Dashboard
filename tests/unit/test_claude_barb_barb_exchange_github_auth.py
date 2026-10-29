"""Barb exchange GitHub writes must use App installation tokens (#3342)."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from scripts import claude_barb_channel as ch

USER_TOKEN = "ghp_" + "u" * 36
APP_TOKEN = "ghs_" + "a" * 36
REPO = "D-sorganization/Barbara"


def test_gh_create_issue_uses_app_token_not_ambient_gh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", USER_TOKEN)
    monkeypatch.setenv("GITHUB_TOKEN", USER_TOKEN)
    captured: dict[str, Any] = {}

    def fake_env(repo: str) -> dict[str, str]:
        assert repo == REPO
        return {"PATH": "/usr/bin", "GH_TOKEN": APP_TOKEN, "GITHUB_TOKEN": APP_TOKEN}

    def fake_run(cmd: list[str], **kwargs: Any) -> Any:
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")

        class Done:
            stdout = "https://github.com/D-sorganization/Barbara/issues/1\n"
            stderr = ""

        return Done()

    monkeypatch.setattr(ch, "barb_exchange_github_env", fake_env)
    monkeypatch.setattr(ch.subprocess, "run", fake_run)

    url = ch.gh_create_issue(REPO, {"title": "t", "body": "b", "labels": ["barb-inbox"]})
    assert url.endswith("/issues/1")
    env = captured["env"]
    assert env is not None
    assert env.get("GH_TOKEN") == APP_TOKEN
    assert env.get("GITHUB_TOKEN") == APP_TOKEN
    assert USER_TOKEN not in env.values()


def test_gh_create_issue_fails_closed_when_app_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", USER_TOKEN)

    def boom(_repo: str) -> dict[str, str]:
        raise ch.BarbExchangeAuthError("GitHub App is not configured for Barb exchange writes")

    monkeypatch.setattr(ch, "barb_exchange_github_env", boom)
    with pytest.raises(ch.BarbExchangeAuthError, match="not configured"):
        ch.gh_create_issue(REPO, {"title": "t", "body": "b", "labels": []})


def test_barb_exchange_token_never_appears_in_logs_or_subprocess_output(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="claude_barb.channel")

    def fake_env(_repo: str) -> dict[str, str]:
        return {"GH_TOKEN": APP_TOKEN, "GITHUB_TOKEN": APP_TOKEN}

    def fake_run(_cmd: list[str], **kwargs: Any) -> Any:
        class Done:
            stdout = APP_TOKEN + "\n"  # gh must not echo token; guard either way
            stderr = ""

        return Done()

    monkeypatch.setattr(ch, "barb_exchange_github_env", fake_env)
    monkeypatch.setattr(ch.subprocess, "run", fake_run)

    with pytest.raises(ch.BarbExchangeAuthError, match="token"):
        ch.gh_create_issue(REPO, {"title": "t", "body": "b", "labels": []})

    assert APP_TOKEN not in caplog.text


def test_barb_exchange_github_env_mints_scoped_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", USER_TOKEN)
    monkeypatch.setattr(ch, "github_app_auth_configured", lambda: True)

    class FakeTok:
        token = APP_TOKEN
        expires_at = 9_999_999_999.0

    seen: dict[str, Any] = {}

    def fake_mint(**kwargs: Any) -> FakeTok:
        seen.update(kwargs)
        return FakeTok()

    monkeypatch.setattr(ch, "mint_installation_access_token", fake_mint)
    env = ch.barb_exchange_github_env(REPO)
    assert env["GH_TOKEN"] == APP_TOKEN
    assert USER_TOKEN not in env.values()
    assert seen["repositories"] == [REPO]
    assert seen["permissions"]["issues"] == "write"


def test_barb_exchange_github_env_raises_when_app_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(ch, "github_app_auth_configured", lambda: False)
    with pytest.raises(ch.BarbExchangeAuthError, match="not configured"):
        ch.barb_exchange_github_env(REPO)
