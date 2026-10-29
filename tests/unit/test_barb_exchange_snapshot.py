"""rd-barb -> Barbara snapshot: allowlisted sections, withheld rows, stripped secrets, fail-closed writes."""

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import barb_exchange_snapshot as snap

NOW = 1_800_000_000.0
PRIVATE = snap.DEFAULT_PRIVATE_REPOS


def _entry(**over: Any) -> dict[str, Any]:
    base = {"id": "e1", "entry_type": "session_digest", "created_at": "2026-10-27T20:00:00Z"}
    return {**base, "body": {"summary": "Lane plan drafted"}, **over}


def _pack(*digests: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"session_digests": list(digests), "open_tasks": [], "directives": [], "holds": [], **extra}


def _clean(pack: Any) -> dict[str, Any]:
    out, problems = snap.clean(pack, PRIVATE, NOW)
    assert problems == [] and out is not None
    return out


def test_clean_keeps_fleet_rows_and_labels_the_snapshot() -> None:
    out = _clean(_pack(_entry()))
    assert out["schema"] == snap.SCHEMA and out["source"] == "rd-barb"
    assert out["generated_at"] == "2027-01-15T08:00:00Z"
    assert out["sections"]["session_digests"] == [_entry()] and out["withheld"] == {}
    assert snap.verify(out) == []


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (_entry(visibility="barb-only"), "barb_only"),
        (_entry(entry_type="transcript"), "transcript"),
        ("x", "not_an_object"),
    ],
)
def test_clean_withholds_rows_that_must_not_leave_the_hub(row: Any, reason: str) -> None:
    out = _clean(_pack(row))
    assert out["sections"]["session_digests"] == [] and out["withheld"] == {reason: 1}


def test_unknown_sections_never_leave_the_hub() -> None:
    out = _clean(_pack(raw_db={"rows": 1}))
    assert "raw_db" not in json.dumps(out) and out["withheld"] == {"unknown_section": 1}


@pytest.mark.parametrize("where", [{"repo": "D-sorganization/Tools_Private"}, {"body": {"repo": "tools_private"}}])
def test_private_repo_rows_shrink_to_an_opaque_id(where: dict[str, Any]) -> None:
    row = _entry(**where)
    row["body"] = {**row["body"], "summary": "Glass bath FEA margin is thin"} if "repo" in where else row["body"]
    out = _clean(_pack(row))
    kept = out["sections"]["session_digests"][0]
    assert kept["withheld"] is True and kept["repo"].startswith("private:") and "body" not in kept
    assert "Tools_Private" not in json.dumps(out) and "Glass bath" not in json.dumps(out)
    assert out["withheld"] == {"private_repo": 1} and kept["repo"] == snap._opaque("tools_private")


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("token ghp_" + "a" * 36, "ghp_"),
        ("reach 192.168.1.20", "192.168"),
        ("mail dieter@example.com", "@example.com"),
        ("hub at controltower.tail1234.ts.net", "ts.net"),
        ("box nas.local", "nas.local"),
    ],
)
def test_clean_strips_secrets_addresses_emails_and_hosts(text: str, gone: str) -> None:
    out = _clean(_pack(_entry(body={"summary": text})))
    assert gone not in json.dumps(out) and "[REDACTED" in json.dumps(out)


def test_dict_keys_are_stripped_too() -> None:
    out = _clean(_pack(_entry(body={"192.168.0.9": "x"})))
    assert "192.168" not in json.dumps(out)


def test_clean_rejects_a_non_object_pack() -> None:
    assert snap.clean([1], PRIVATE, NOW) == (None, ["context pack must be a JSON object"])


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda s: {**s, "schema": "v0"}, "schema"),
        (lambda s: {**s, "sections": {"raw": []}}, "sections"),
        (lambda s: {**s, "sections": {"holds": "x"}}, "list"),
        (lambda s: {**s, "notice": "Bearer " + "x" * 30}, "secret"),
        (lambda s: {**s, "sections": {"holds": [{"visibility": "barb-only"}]}}, "barb-only"),
    ],
)
def test_verify_rejects_unsafe_or_malformed_snapshots(mutate: Any, needle: str) -> None:
    assert any(needle in p for p in snap.verify(mutate(_clean(_pack()))))
    assert snap.verify("nope") == ["snapshot must be a JSON object"]


def test_private_repo_config_never_goes_empty() -> None:
    assert snap.private_repos_from({}) == PRIVATE
    assert snap.private_repos_from({"BARB_EXCHANGE_PRIVATE_REPOS": " , "}) == PRIVATE
    assert snap.private_repos_from({"BARB_EXCHANGE_PRIVATE_REPOS": "A, b"}) == {"a", "b"}


def test_publish_is_dry_run_unless_switched_on_and_fails_closed() -> None:
    calls: list[tuple[str, str, str]] = []

    def writer(repo: str, path: str, content: str) -> str:
        calls.append((repo, path, content))
        return "sha1"

    good = _clean(_pack(_entry()))
    assert snap.publish(good, "o/r", writer, live=False)["status"] == "dry_run" and calls == []
    assert snap.publish(good, "", writer, live=True)["error"] == "config_missing" and calls == []
    assert snap.publish({**good, "schema": "x"}, "o/r", writer, live=True)["status"] == "rejected"
    assert snap.publish(good, "o/r", writer, live=True) == {
        "ok": True,
        "status": "written",
        "path": snap.SNAPSHOT_PATH,
        "ref": "sha1",
    }
    assert json.loads(calls[0][2]) == good

    def broken(repo: str, path: str, content: str) -> str:
        raise OSError("down")

    assert snap.publish(good, "o/r", broken, live=True)["error"] == "write_failed"


def test_kill_switch_reads_only_explicit_on() -> None:
    envs: list[dict[str, str]] = [{}, {"BARB_EXCHANGE_SNAPSHOT": "off"}, {"BARB_EXCHANGE_SNAPSHOT": "ON"}]
    assert [snap.snapshot_live(e) for e in envs] == [False, False, True]


def test_cli_clean_then_verify_round_trip(tmp_path: Any, capsys: Any) -> None:
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps(_pack(_entry(), _entry(id="e2", visibility="barb-only"))), encoding="utf-8")
    assert snap.main(["clean", "--pack", str(pack)]) == 0
    out = json.loads(capsys.readouterr().out)["snapshot"]
    (tmp_path / "snap.json").write_text(json.dumps(out), encoding="utf-8")
    assert snap.main(["verify", "--snapshot", str(tmp_path / "snap.json")]) == 0
    assert snap.main(["verify", "--snapshot", str(tmp_path / "missing.json")]) == 1


def test_no_private_topology_in_this_file() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    assert "controltower" not in source.lower()
    assert "tail1234" not in source.lower()
