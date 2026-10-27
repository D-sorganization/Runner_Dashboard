from __future__ import annotations

import json
from typing import Any

import fleetctl
import pytest
from fleet_client import FleetAPIError


def test_dispatch_fails_over_and_reuses_idempotency_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self: Any, method: str, path: str, **kwargs: Any) -> Any:
        calls.append((self.base_url, path, kwargs))
        if self.base_url == "https://first.example":
            raise FleetAPIError(0, {"error": "unreachable", "reason": "offline"})
        if path == "/api/v1/staff/summary":
            return {"ok": True}
        return {"run_id": "run-1"}

    monkeypatch.setattr(fleetctl.FleetClient, "request", request)
    monkeypatch.setenv("FLEET_API_URLS", "https://first.example,https://second.example")
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("work", encoding="utf-8")
    code = fleetctl.main(
        [
            "dispatch", "--role", "night-watch", "--repo", "Runner_Dashboard",
            "--prompt-file", str(prompt),
        ]
    )
    assert code == 0
    key = calls[-1][2]["headers"]["Idempotency-Key"]
    assert len(key) == 64
    assert calls[-2][1] == "/api/v1/staff/summary"
    assert calls[-1][0] == "https://second.example"


def test_dispatch_hold_does_not_fall_through(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    calls: list[str] = []

    def request(self: Any, method: str, path: str, **kwargs: Any) -> Any:
        calls.append(path)
        if path.endswith("/summary"):
            return {"ok": True}
        raise FleetAPIError(423, {"detail": "held"})

    monkeypatch.setattr(fleetctl.FleetClient, "request", request)
    code = fleetctl.main(
        ["dispatch", "--role", "night-watch", "--repo", "Runner_Dashboard", "--issue", "8"]
    )
    assert code == 5
    assert calls == ["/api/v1/staff/summary", "/api/v1/staff/night-watch/run"]
    assert json.loads(capsys.readouterr().out)["result"] == "held"


def test_dispatch_requires_a_prompt_source_for_prompt_only(capsys: Any) -> None:
    code = fleetctl.main(["dispatch", "--role", "night-watch", "--repo", "Runner_Dashboard"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_arguments"
