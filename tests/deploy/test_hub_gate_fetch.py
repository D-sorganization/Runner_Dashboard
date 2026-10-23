"""Hub gate should use lightweight /api/version, not full fleet status (#2110)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from node_auto_deploy.hub_gate import fetch_fleet_status  # noqa: E402

_SHA = "a" * 40


def test_fetch_fleet_status_reads_version_endpoint() -> None:
    version_body = json.dumps({"git_sha": _SHA, "dashboard": "4.0.0"}).encode()

    class _Resp:
        def read(self) -> bytes:
            return version_body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

    with patch("node_auto_deploy.hub_gate.urlopen", return_value=_Resp()) as mock_open:
        payload = fetch_fleet_status("http://hub:8321", "token")

    req = mock_open.call_args[0][0]
    assert req.full_url.endswith("/api/version")
    assert payload["hub"]["deployment"]["git_sha"] == _SHA
