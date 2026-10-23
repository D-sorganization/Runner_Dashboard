from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from library.db import init_library_db
from library.retrieval_ledger import record_retrievals
from library.verify import verify_content


@pytest.fixture(autouse=True)
def _local_node(monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard_config

    monkeypatch.setattr(dashboard_config, "HOSTNAME", "desk", raising=False)


def _indexed(tmp_path: Path) -> tuple[Path, str]:
    db = init_library_db(tmp_path / "library.sqlite")
    source = tmp_path / "source" / "paper.md"
    source.parent.mkdir()
    source.write_text("Rotational power improves turbine efficiency.", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO works(id,sha256,author,title,format) VALUES(1,?,?,?,?)",
            (digest, "Doe", "Rotational Power", "md"),
        )
        conn.execute(
            "INSERT INTO files(id,work_id,source_id,rel_path,mtime,size,sha256,source_root) "
            "VALUES(1,1,'shared','paper.md',0,1,?,?)",
            (digest, str(source.parent)),
        )
        conn.execute(
            "INSERT INTO passages(id,work_id,page_start,page_end,section,text,token_estimate) "
            "VALUES(19,1,7,8,'Efficiency','Rotational power improves turbine efficiency.',6)"
        )
    return db, digest


def test_verify_resolves_canonical_and_lib_citations_by_hash_after_move(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    (tmp_path / "source").rename(tmp_path / "moved")
    content = (
        f"Rotational power improves turbine efficiency. "
        f"[ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8] "
        f"lib://shared/w1/p19@{digest[:8]}#p7-8"
    )

    result = verify_content(db, content)

    assert [item["verified"] for item in result["citations"]] == [True, True]
    assert all(item["work"]["title"] == "Rotational Power" for item in result["citations"])


def test_verify_rejects_hash_page_and_claim_mismatches(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    content = "Dinosaurs live on Mars. [ref:desk:shared:w1:p19#sha256=00000000:p.2-4]"

    result = verify_content(db, content)

    assert result["citations"][0]["status"] == "sha_mismatch"
    content = f"Dinosaurs live on Mars. [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.2-4]"
    assert verify_content(db, content)["citations"][0]["status"] == "page_range_mismatch"
    content = f"Dinosaurs live on Mars. [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    assert verify_content(db, content)["citations"][0]["status"] == "misappropriated_reference"
    content = f"This function is not safe. [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    assert verify_content(db, content)["citations"][0]["status"] == "misappropriated_reference"
    claim = "Rotational power does not improve turbine efficiency."
    content = f"{claim} [ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    assert verify_content(db, content)["citations"][0]["status"] == "misappropriated_reference"


def test_verify_reports_broken_relative_links(tmp_path: Path) -> None:
    db, _ = _indexed(tmp_path)
    result = verify_content(db, "See [missing](docs/absent.md#overview).", repo_root=tmp_path)
    assert result["links"] == [{"url": "docs/absent.md#overview", "verified": False, "status": "broken_link"}]


def test_verify_checks_markdown_heading_anchors_and_legacy_markers(tmp_path: Path) -> None:
    db, _ = _indexed(tmp_path)
    target = tmp_path / "guide.md"
    target.write_text("# Getting Started\n", encoding="utf-8")

    result = verify_content(
        db,
        "[valid](guide.md#getting-started) [invalid](guide.md#missing) (work_id=1)",
        repo_root=tmp_path,
    )

    assert [link["verified"] for link in result["links"]] == [True, False]
    assert result["citations"][0]["status"] == "legacy_unverifiable"


def test_verify_requires_run_ledger_membership(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    cite = f"[ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, cite, run_id="run-1")

    assert result["citations"][0]["status"] == "unretrieved_reference"
    record_retrievals(
        db,
        run_id="run-1",
        references=[
            {
                "node": "desk",
                "source_id": "shared",
                "work_id": 1,
                "passage_id": 19,
                "file_sha256": digest,
                "page_start": 7,
                "page_end": 8,
            }
        ],
    )
    assert verify_content(db, cite, run_id="run-1")["citations"][0]["verified"] is True
    lib_cite = f"lib://shared/w1/p19@{digest[:8]}#p7-8"
    assert (
        verify_content(db, f"Rotational power improves turbine efficiency. {lib_cite}", run_id="run-1")["citations"][0][
            "verified"
        ]
        is True
    )


def test_verify_requires_exact_node_in_run_ledger(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    record_retrievals(
        db,
        run_id="run-1",
        references=[
            {
                "node": "desk",
                "source_id": "shared",
                "work_id": 1,
                "passage_id": 19,
                "file_sha256": digest,
                "page_start": 7,
                "page_end": 8,
            }
        ],
    )
    cite = f"[ref:other-node:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}", run_id="run-1")

    assert result["citations"][0]["status"] == "node_mismatch"
    assert result["citations"][0]["verified"] is False


def test_remote_citation_does_not_resolve_against_colliding_local_ids(tmp_path: Path, monkeypatch) -> None:
    db, digest = _indexed(tmp_path)
    calls = []

    def verify_peer(match, content, *, deadline, fleet_nodes, expected_sha256=None):
        calls.append((match.group("node"), match.group(0)))
        return {"citation": match.group(0), "verified": False, "status": "peer_unavailable"}

    monkeypatch.setattr("library.verify._verify_peer_citation", verify_peer, raising=False)
    cite = f"[ref:other-node:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}")

    assert calls == [("other-node", cite)]
    assert result["citations"][0]["status"] == "peer_unavailable"
    assert result["citations"][0]["verified"] is False


@pytest.mark.parametrize("fault", [None, "multiple", "citation", "source", "digest", "page", "timeout", "malformed"])
def test_remote_citation_uses_only_configured_peer_and_returns_checked_metadata(
    tmp_path: Path, monkeypatch, fault: str | None
) -> None:
    import dashboard_config
    import library.verify as verifier

    db, digest = _indexed(tmp_path)
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", {"peer": "https://peer.example"}, raising=False)
    monkeypatch.setenv("HUB_FLEET_TOKEN", "test-peer-token")
    cite = f"[ref:peer:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    cite2 = f"[ref:peer:shared:w1:p20#sha256={digest[:8]}:p.9-10]"
    observed: dict[str, object] = {}
    forwarded: list[str] = []
    remote_citation = "[ref:other:shared:w1:p19#sha256=00000000:p.7-8]" if fault == "citation" else cite
    remote_source = "wrong-source" if fault == "source" else "shared"
    remote_digest = ("f" if digest[0] != "f" else "e") * 64 if fault == "digest" else digest
    remote_page_start = 6 if fault == "page" else 7

    def handle(request: httpx.Request) -> httpx.Response:
        observed["url"] = str(request.url)
        observed["authorization"] = request.headers.get("Authorization")
        request_content = json.loads(request.read())["content"]
        forwarded.append(request_content)
        selected = cite2 if fault == "multiple" and cite2 in request_content else cite
        passage_id, page_start = (20, 9) if selected == cite2 else (19, 7)
        if fault == "timeout":
            raise httpx.ReadTimeout("peer timed out", request=request)
        if fault == "malformed":
            return httpx.Response(200, content=b"not-json")
        return httpx.Response(
            200,
            json={
                "verified": True,
                "citations": [
                    {
                        "citation": remote_citation if fault == "citation" else selected,
                        "node": "peer",
                        "verified": True,
                        "status": "verified",
                        "work": {"id": 1, "title": "Remote title"},
                        "file": {"source_id": remote_source, "sha256": remote_digest},
                        "passage": {
                            "id": passage_id,
                            "page_start": remote_page_start if selected == cite else page_start,
                            "page_end": 10 if selected == cite2 else 8,
                        },
                    }
                ],
                "links": [],
            },
        )

    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(verifier.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    content = f"PRIVATE EARLIER CONTENT {('x' * 600)} Rotational power improves turbine efficiency. {cite}"
    if fault == "multiple":
        content += f"\nTurbine rotational power increases efficiency. {cite2}"
    result = verify_content(db, content)

    expected = [fault in (None, "multiple")] * (2 if fault == "multiple" else 1)
    assert [item["verified"] for item in result["citations"]] == expected
    if fault not in (None, "multiple"):
        assert result["citations"][0]["status"] == "peer_unavailable"
    assert observed["url"] == "https://peer.example/api/v1/library/verify"
    assert observed["authorization"] == "Bearer test-peer-token"
    assert all("PRIVATE EARLIER CONTENT" not in text and text.count("[ref:") == 1 for text in forwarded)


def test_peer_stream_has_one_wall_clock_deadline(monkeypatch) -> None:
    import asyncio
    import time

    import library.verify as verifier

    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(5):
                await asyncio.sleep(0.04)
                yield b"{}"

        async def aclose(self) -> None: ...

    transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=SlowStream()))
    client_type = httpx.AsyncClient
    monkeypatch.setattr(verifier.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        asyncio.run(verifier._fetch_peer_response("https://peer.example", "token", "content", 0.1))

    assert time.monotonic() - started < 0.3


def test_remote_run_reference_is_rejected_before_peer_lookup(tmp_path: Path, monkeypatch) -> None:
    db, digest = _indexed(tmp_path)
    cite = f"[ref:peer:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    monkeypatch.setattr(
        "library.verify._verify_peer_citation",
        lambda *args, **kwargs: pytest.fail("peer lookup preceded hub ledger check"),
    )

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}", run_id="run-absent")

    assert result["citations"][0]["status"] == "unretrieved_reference"


def test_remote_run_ledger_hash_is_bound_before_peer_lookup(tmp_path: Path, monkeypatch) -> None:
    db, digest = _indexed(tmp_path)
    record_retrievals(
        db,
        run_id="run-1",
        references=[
            {
                "node": "peer",
                "source_id": "shared",
                "work_id": 1,
                "passage_id": 19,
                "file_sha256": digest,
                "page_start": 7,
                "page_end": 8,
            }
        ],
    )
    observed: list[set[str] | None] = []

    def verify_peer(match, content, *, deadline, fleet_nodes, expected_sha256):
        observed.append(expected_sha256)
        return {"citation": match.group(0), "verified": False, "status": "peer_unavailable"}

    monkeypatch.setattr("library.verify._verify_peer_citation", verify_peer)
    cite = f"[ref:peer:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    verify_content(db, f"Rotational power improves turbine efficiency. {cite}", run_id="run-1")

    assert observed == [{digest}]


def test_peer_local_only_mode_never_forwards_remote_citation(tmp_path: Path, monkeypatch) -> None:
    db, digest = _indexed(tmp_path)
    monkeypatch.setattr(
        "library.verify._verify_peer_citation",
        lambda *args, **kwargs: pytest.fail("local-only peer verification recursed"),
    )
    cite = f"[ref:peer:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}", local_only=True)

    assert result["citations"][0]["status"] == "node_mismatch"
    assert result["citations"][0]["verified"] is False


@pytest.mark.parametrize("configured,token", [(False, "secret"), (True, "")])
def test_missing_peer_configuration_or_token_fails_without_network(
    tmp_path: Path, monkeypatch, configured: bool, token: str
) -> None:
    import dashboard_config
    import library.verify as verifier

    db, digest = _indexed(tmp_path)
    nodes = {"peer": "https://peer.example"} if configured else {}
    monkeypatch.setattr(dashboard_config, "FLEET_NODES", nodes, raising=False)
    if token:
        monkeypatch.setenv("HUB_FLEET_TOKEN", token)
    else:
        monkeypatch.delenv("HUB_FLEET_TOKEN", raising=False)
    monkeypatch.setattr(verifier.httpx, "AsyncClient", lambda **kwargs: pytest.fail("unexpected peer request"))
    cite = f"[ref:peer:shared:w1:p19#sha256={digest[:8]}:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}")

    assert result["citations"][0]["status"] == "peer_unavailable"
    assert result["citations"][0]["verified"] is False


def test_valid_reference_cannot_hide_a_malformed_reference(tmp_path: Path) -> None:
    db, digest = _indexed(tmp_path)
    cite = f"[ref:desk:shared:w1:p19#sha256={digest[:8]}:p.7-8]"
    invalid = "[ref:desk:shared:w1:p19#sha256=typo:p.7-8]"

    result = verify_content(db, f"Rotational power improves turbine efficiency. {cite}\n{invalid}")

    assert result["verified"] is False
    assert [item["verified"] for item in result["citations"]] == [True, False]
    assert result["citations"][1]["status"] == "invalid_citation"
