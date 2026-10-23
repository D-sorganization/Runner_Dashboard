"""Pooled evidence must retain identity and respect sharing boundaries."""

from pathlib import Path

import httpx
import pytest
from library.db import init_library_db
from library.ingest import ingest_sources
from library.pool import federated_search
from library.search import SearchOptions, search_passages
from library.sources import add_source


@pytest.mark.asyncio
async def test_remote_identity_deduplicates_content_without_losing_distinct_passages() -> None:
    digest = "a" * 64

    def respond(request: httpx.Request) -> httpx.Response:
        offset = 10 if request.url.host == "one" else 20
        return httpx.Response(
            200,
            json={
                "passages": [
                    {
                        "passage_id": offset + page,
                        "work_id": offset,
                        "source_id": "books",
                        "node": "spoofed",
                        "file_sha256": digest,
                        "work_sha256": digest,
                        "page_start": page,
                        "page_end": page,
                        "text": f"evidence page {page}",
                        "token_estimate": 3,
                    }
                    for page in (1, 2)
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await federated_search(
            "evidence",
            k=10,
            fleet_nodes={"first": "http://one", "second": "http://two"},
            client=client,
            local_search_fn=lambda *args, **kwargs: [],
        )
    assert len(result.passages) == 2
    assert {hit["page_start"] for hit in result.passages} == {1, 2}
    for hit in result.passages:
        assert hit["node"] in {"first", "second"}
        assert hit["file_sha256"] == digest
        assert "#sha256=aaaaaaaa:" in hit["cite_key"]
        assert "@aaaaaaaa#" in hit["cite_uri"]


@pytest.mark.asyncio
async def test_merged_result_honors_requested_k() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "passages": [
                        {
                            "passage_id": 1,
                            "work_id": 1,
                            "text": str(request.url.host),
                            "token_estimate": 1,
                        }
                    ]
                },
            )
        )
    ) as client:
        result = await federated_search(
            "evidence",
            k=1,
            fleet_nodes={"one": "http://one", "two": "http://two"},
            client=client,
            local_search_fn=lambda *args, **kwargs: [],
        )
    assert len(result.passages) == 1


@pytest.mark.asyncio
async def test_invalid_peer_digest_is_unavailable_instead_of_citable() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"passages": [{"file_sha256": "not-a-digest"}]})
        )
    ) as client:
        result = await federated_search(
            "evidence",
            fleet_nodes={"peer": "http://peer"},
            client=client,
            local_search_fn=lambda *args, **kwargs: [],
        )
    assert result.passages == []
    assert result.unavailable_sources == ["peer"]


@pytest.mark.asyncio
async def test_local_pool_uses_shared_copy_and_excludes_private_work(tmp_path: Path) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    for source_id, shared in (("private", False), ("public", True)):
        root = tmp_path / source_id
        root.mkdir()
        add_source(db, source_id=source_id, label=source_id, root_path=root, shared=shared)
        (root / "Shared.txt").write_text("evidence common shared copy", encoding="utf-8")
        if not shared:
            (root / "Secret.txt").write_text("evidence confidential private work", encoding="utf-8")
    ingest_sources(db)
    result = await federated_search("evidence", db_path=db, fleet_nodes={})
    assert result.passages
    assert all(hit["source_id"] == "public" for hit in result.passages)
    assert all("confidential" not in hit["text"] for hit in result.passages)


def test_shared_search_cannot_expand_through_unfiltered_vectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = init_library_db(tmp_path / "library.sqlite")
    monkeypatch.setattr("library.hybrid_search.hybrid_enabled", lambda: True)

    def unsafe_expansion(*args: object, **kwargs: object) -> list:
        pytest.fail("shared search must not use an unfiltered vector expansion")

    monkeypatch.setattr("library.hybrid_search.hybrid_search_passages", unsafe_expansion)
    assert search_passages(db, "evidence", options=SearchOptions(shared_only=True)) == []
