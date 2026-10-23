"""External browser configuration must not change tailnet OAuth semantics."""

import pytest
from browser_origin import browser_origin, external_browser_request
from starlette.requests import Request

pytestmark = pytest.mark.unit


def request(host: str, **headers: str) -> Request:
    return Request(
        {"type": "http", "headers": [(k.encode(), v.encode()) for k, v in {"host": host, **headers}.items()]}
    )


def test_oauth_tailnet_origin_does_not_select_external_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("DASHBOARD_PUBLIC_ORIGIN", "https://desk.example.ts.net")
    assert browser_origin() is None
    assert not external_browser_request(request("desk.example.ts.net"))


def test_explicit_browser_origin_selects_only_exact_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", "https://ops.example.invalid")
    assert browser_origin() == "https://ops.example.invalid"
    assert external_browser_request(request("OPS.example.invalid"))
    assert not external_browser_request(request("ops.example.invalid.evil.invalid"))
    assert not external_browser_request(request("desk.example.ts.net", origin="https://ops.example.invalid"))


@pytest.mark.parametrize(
    "value",
    [
        "http://ops.invalid",
        "https://user:pass@ops.invalid",  # pragma: allowlist secret — intentional invalid Basic Auth URL.
        "https://ops.invalid/",
        "https://ops.invalid?q=x",
        "https://ops.invalid#x",
        "https://[broken",
        "https://ops.invalid:bad",
        "https://ops.invalid\\evil",
    ],
)
def test_invalid_browser_origin_fails_closed(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", value)
    assert browser_origin() is None


@pytest.mark.parametrize("header", ["cf-ray", "cf-connecting-ip", "cf-access-jwt-assertion", "cf-visitor"])
def test_edge_markers_always_select_strict_plane(monkeypatch: pytest.MonkeyPatch, header: str) -> None:
    monkeypatch.delenv("DASHBOARD_BROWSER_PUBLIC_ORIGIN", raising=False)
    assert external_browser_request(request("localhost", **{header: "untrusted-marker"}))
