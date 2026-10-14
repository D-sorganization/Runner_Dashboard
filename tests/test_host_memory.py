"""Tests for host_memory (RM #2071): graceful degradation and the nonpaged-pool alert."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import host_memory as hm  # noqa: E402

HEADER = "time,nonpaged_gb,paged_gb,system_handles,free_gb,commit_gb,vmmemwsl_gb\n"


def _csv(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "kernel-pool.csv"
    path.write_text(HEADER + "".join(r + "\n" for r in rows), encoding="utf-8")
    return path


def test_kernel_pool_missing_is_unavailable(tmp_path: Path) -> None:
    out = hm.read_kernel_pool_tail([tmp_path / "nope.csv"])
    assert out["status"] == "unavailable"
    assert "latest" not in out


def test_kernel_pool_header_only_is_unavailable(tmp_path: Path) -> None:
    assert hm.read_kernel_pool_tail([_csv(tmp_path, [])])["status"] == "unavailable"


def test_kernel_pool_tail_keeps_last_rows_and_latest(tmp_path: Path) -> None:
    rows = [f"2026-10-11T15:{i:02d}:00,2.{i},2.0,100,70.0,50.0,10.0" for i in range(10)]
    out = hm.read_kernel_pool_tail([_csv(tmp_path, rows)], rows=3)
    assert out["status"] == "ok"
    assert [s["nonpaged_gb"] for s in out["samples"]] == [2.7, 2.8, 2.9]
    assert out["latest"]["vmmemwsl_gb"] == 10.0


def test_kernel_pool_bad_numbers_become_none_not_zero(tmp_path: Path) -> None:
    out = hm.read_kernel_pool_tail([_csv(tmp_path, ["t,abc,,1,2,3,nan"])])
    latest = out["latest"]
    assert latest["nonpaged_gb"] is None and latest["paged_gb"] is None and latest["vmmemwsl_gb"] is None


def test_kernel_pool_skips_missing_candidate_to_next(tmp_path: Path) -> None:
    good = _csv(tmp_path, ["t,1,1,1,1,1,1"])
    assert hm.read_kernel_pool_tail([tmp_path / "x.csv", good])["status"] == "ok"


def test_parse_memory_cap() -> None:
    assert hm.parse_memory_cap_gb("[wsl2]\nmemory=24GB\n") == 24.0
    assert hm.parse_memory_cap_gb("memory = 24576MB") == 24.0
    assert hm.parse_memory_cap_gb("[wsl2]\nprocessors=4") is None
    assert hm.parse_memory_cap_gb("memory=12XB") is None


def test_vmmem_unavailable_without_sample() -> None:
    assert hm.read_vmmem(hm.unavailable("x"), 24.0)["status"] == "unavailable"


def test_vmmem_percent_of_cap_and_no_cap() -> None:
    pool = {"status": "ok", "latest": {"vmmemwsl_gb": 12.0}}
    assert hm.read_vmmem(pool, 24.0)["percent_of_cap"] == 50.0
    nocap = hm.read_vmmem(pool, None)
    assert nocap["status"] == "ok" and nocap["percent_of_cap"] is None


def test_commit_probe_none_and_errors_are_unavailable() -> None:
    assert hm.read_commit(lambda: None)["status"] == "unavailable"

    def boom() -> tuple[float, float]:
        raise OSError("denied")

    assert hm.read_commit(boom)["status"] == "unavailable"
    assert hm.read_commit(lambda: (0.0, 0.0))["status"] == "unavailable"


def test_commit_percent() -> None:
    out = hm.read_commit(lambda: (100.0, 40.0))
    assert out == {"status": "ok", "commit_gb": 60.0, "limit_gb": 100.0, "percent": 60.0}


def test_cache_trim_missing_journalctl_unavailable() -> None:
    def run(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("journalctl")

    assert hm.read_cache_trim_last_run(run)["status"] == "unavailable"


def test_cache_trim_empty_and_garbage_unavailable() -> None:
    assert (
        hm.read_cache_trim_last_run(lambda *a, **k: SimpleNamespace(returncode=0, stdout=""))["status"] == "unavailable"
    )
    assert (
        hm.read_cache_trim_last_run(lambda *a, **k: SimpleNamespace(returncode=0, stdout="{x"))["status"]
        == "unavailable"
    )
    assert (
        hm.read_cache_trim_last_run(lambda *a, **k: SimpleNamespace(returncode=1, stdout="{}"))["status"]
        == "unavailable"
    )


def test_cache_trim_timeout_unavailable() -> None:
    def run(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired("journalctl", 3)

    assert hm.read_cache_trim_last_run(run)["status"] == "unavailable"


def test_cache_trim_last_run_parsed() -> None:
    entry = json.dumps({"__REALTIME_TIMESTAMP": "1760198400000000", "MESSAGE": "cache 40% -> 5%"})
    out = hm.read_cache_trim_last_run(lambda *a, **k: SimpleNamespace(returncode=0, stdout=entry + "\n"))
    assert out["status"] == "ok"
    assert out["last_run"].startswith("2025-10-11T")
    assert out["message"] == "cache 40% -> 5%"


def test_alert_threshold_is_three_gb_and_strict() -> None:
    assert hm.NONPAGED_POOL_ALERT_GB == 3.0

    def pool(v: float) -> dict:
        return {"status": "ok", "latest": {"nonpaged_gb": v}}

    assert hm.evaluate_alerts(pool(3.0)) == []
    alerts = hm.evaluate_alerts(pool(3.01))
    assert alerts[0]["code"] == "nonpaged_pool_high" and alerts[0]["threshold_gb"] == 3.0


def test_unavailable_pool_never_alerts() -> None:
    assert hm.evaluate_alerts(hm.unavailable("x")) == []
    assert hm.evaluate_alerts({"status": "ok", "latest": {"nonpaged_gb": None}}) == []


def test_aggregate_all_unavailable(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(hm, "read_kernel_pool_tail", lambda: hm.unavailable("x"))
    monkeypatch.setattr(hm, "read_commit", lambda: hm.unavailable("x"))
    monkeypatch.setattr(hm, "read_wsl_memory_cap_gb", lambda: None)
    monkeypatch.setattr(hm, "read_cache_trim_last_run", lambda: hm.unavailable("x"))
    out = hm.get_host_memory_health()
    assert out["status"] == "unavailable" and out["alerts"] == []


def test_aggregate_alert(monkeypatch) -> None:  # noqa: ANN001
    pool = {"status": "ok", "latest": {"nonpaged_gb": 3.5, "vmmemwsl_gb": 10.0}}
    monkeypatch.setattr(hm, "read_kernel_pool_tail", lambda: pool)
    monkeypatch.setattr(hm, "read_commit", lambda: hm.unavailable("x"))
    monkeypatch.setattr(hm, "read_wsl_memory_cap_gb", lambda: 20.0)
    monkeypatch.setattr(hm, "read_cache_trim_last_run", lambda: hm.unavailable("x"))
    out = hm.get_host_memory_health()
    assert out["status"] == "alert" and out["vmmem"]["percent_of_cap"] == 50.0


def _codes(alerts: list[dict]) -> list[str]:
    return [a["code"] for a in alerts]


def test_commit_alert_default_90_strict() -> None:
    assert hm.COMMIT_PERCENT_ALERT == 90.0
    pool = hm.unavailable("x")
    assert hm.evaluate_alerts(pool, commit={"status": "ok", "percent": 90.0}) == []
    alerts = hm.evaluate_alerts(pool, commit={"status": "ok", "percent": 90.1})
    assert _codes(alerts) == ["commit_high"]
    assert alerts[0]["value_percent"] == 90.1 and alerts[0]["threshold_percent"] == 90.0


def test_vmmem_alert_default_90_strict() -> None:
    assert hm.VMMEM_PERCENT_ALERT == 90.0
    pool = hm.unavailable("x")
    ok = {"status": "ok", "percent_of_cap": 90.0}
    assert hm.evaluate_alerts(pool, vmmem=ok) == []
    alerts = hm.evaluate_alerts(pool, vmmem={"status": "ok", "percent_of_cap": 95.0})
    assert _codes(alerts) == ["vmmem_near_cap"]
    assert alerts[0]["threshold_percent"] == 90.0


def test_unavailable_or_capless_sources_never_alert() -> None:
    pool = hm.unavailable("x")
    assert hm.evaluate_alerts(pool, commit=hm.unavailable("x"), vmmem=hm.unavailable("x")) == []
    assert hm.evaluate_alerts(pool, vmmem={"status": "ok", "percent_of_cap": None}) == []
    assert hm.evaluate_alerts(pool, commit={"status": "ok", "percent": None}) == []


def test_all_three_alerts_together_and_custom_thresholds() -> None:
    pool = {"status": "ok", "latest": {"nonpaged_gb": 4.0}}
    alerts = hm.evaluate_alerts(
        pool,
        commit={"status": "ok", "percent": 50.0},
        vmmem={"status": "ok", "percent_of_cap": 60.0},
        commit_percent_threshold=40.0,
        vmmem_percent_threshold=55.0,
    )
    assert _codes(alerts) == ["nonpaged_pool_high", "commit_high", "vmmem_near_cap"]


def test_env_threshold_valid_invalid_and_default(monkeypatch, caplog) -> None:  # noqa: ANN001
    monkeypatch.setenv("T_X", "75")
    assert hm.env_threshold("T_X", 90.0, maximum=100.0) == 75.0
    for bad in ("abc", "nan", "-5", "0", "150"):
        monkeypatch.setenv("T_X", bad)
        caplog.clear()
        assert hm.env_threshold("T_X", 90.0, maximum=100.0) == 90.0
        assert "T_X" in caplog.text
    monkeypatch.delenv("T_X")
    assert hm.env_threshold("T_X", 90.0) == 90.0


def test_aggregate_commit_and_vmmem_alerts(monkeypatch) -> None:  # noqa: ANN001
    pool = {"status": "ok", "latest": {"nonpaged_gb": 1.0, "vmmemwsl_gb": 19.0}}
    monkeypatch.setattr(hm, "read_kernel_pool_tail", lambda: pool)
    monkeypatch.setattr(hm, "read_commit", lambda: {"status": "ok", "percent": 95.0})
    monkeypatch.setattr(hm, "read_wsl_memory_cap_gb", lambda: 20.0)
    monkeypatch.setattr(hm, "read_cache_trim_last_run", lambda: hm.unavailable("x"))
    out = hm.get_host_memory_health()
    assert out["status"] == "alert"
    assert _codes(out["alerts"]) == ["commit_high", "vmmem_near_cap"]


def test_route_returns_payload(monkeypatch) -> None:  # noqa: ANN001
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.system import router

    monkeypatch.setattr(hm, "get_host_memory_health", lambda: {"status": "unavailable"})
    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/system/host-memory").json() == {"status": "unavailable"}
