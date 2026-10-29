"""Contract tests for Private Mode classification levels (issue #3250, epic #3249)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import private_mode as pm  # noqa: E402


def test_level_is_a_total_order() -> None:
    assert pm.Level.PUBLIC < pm.Level.INTERNAL < pm.Level.SENSITIVE
    assert sorted(pm.Level, reverse=True)[0] is pm.Level.SENSITIVE


def test_absent_private_mode_is_disabled() -> None:
    """Flag absent -> PUBLIC everywhere (the safe-disable contract)."""
    config = pm.parse_private_mode(None)
    assert config == pm.DISABLED
    assert config.enabled is False
    assert config.default_level is pm.Level.PUBLIC


def test_explicit_disabled_round_trips() -> None:
    config = pm.parse_private_mode({"enabled": False, "default_level": "PUBLIC"})
    assert config == pm.DISABLED


def test_enabled_with_internal_default() -> None:
    config = pm.parse_private_mode({"enabled": True, "default_level": "INTERNAL"})
    assert config.enabled is True
    assert config.default_level is pm.Level.INTERNAL


@pytest.mark.parametrize("level_name", ["public", "Sensitive", "TOP_SECRET", "", "PUBLICC"])
def test_unsupported_level_name_is_rejected(level_name: str) -> None:
    with pytest.raises(ValueError, match="unsupported level"):
        pm.parse_private_mode({"enabled": True, "default_level": level_name})


def test_non_object_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        pm.parse_private_mode(["enabled"])


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing required key.*default_level"):
        pm.parse_private_mode({"enabled": True})


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown key.*scope"):
        pm.parse_private_mode({"enabled": True, "default_level": "PUBLIC", "scope": "repo"})


@pytest.mark.parametrize(
    ("field", "value"),
    [("enabled", "true"), ("enabled", 1), ("default_level", True), ("default_level", 0)],
)
def test_wrong_value_type_is_rejected(field: str, value: object) -> None:
    data = {"enabled": True, "default_level": "PUBLIC"}
    data[field] = value
    with pytest.raises(ValueError, match=field):
        pm.parse_private_mode(data)


def test_config_is_immutable() -> None:
    config = pm.parse_private_mode(None)
    with pytest.raises(AttributeError):
        config.enabled = True  # type: ignore[misc]


# --- PM-1 (#3251): classification resolver (repo/path globs, operator marks, max-join) ---


def _sample_policy() -> pm.ClassificationPolicy:
    return pm.parse_classification_policy(
        {
            "default_level": "INTERNAL",
            "repos": [
                {"repo": "example-corp/widgets-public", "level": "PUBLIC"},
                {"repo": "example-corp/widgets-internal", "level": "INTERNAL"},
                {"repo": "example-corp/widgets-sensitive", "level": "SENSITIVE"},
            ],
            "path_globs": [
                {"glob": "docs/public/**", "level": "PUBLIC"},
                {"glob": "secrets/**", "level": "SENSITIVE"},
                {"glob": "internal/*.yml", "level": "INTERNAL"},
            ],
            "operator_marks": {
                "example-corp/widgets-public#42": "SENSITIVE",
                "example-corp/widgets-internal#7": "SENSITIVE",
            },
        }
    )


def test_max_join_lattice_properties() -> None:
    levels = tuple(pm.Level)
    for a in levels:
        assert pm.max_join(a) is a
        assert pm.max_join(a, a) is a
        for b in levels:
            assert pm.max_join(a, b) is pm.max_join(b, a)
            assert pm.max_join(a, b) >= a
            assert pm.max_join(a, b) >= b
            for c in levels:
                assert pm.max_join(pm.max_join(a, b), c) is pm.max_join(a, pm.max_join(b, c))
    assert pm.max_join() is pm.Level.PUBLIC


def test_flag_off_resolve_is_always_public() -> None:
    policy = _sample_policy()
    inp = pm.ClassificationInput(
        repo="example-corp/widgets-sensitive",
        paths=("secrets/key.pem",),
        target_id="example-corp/widgets-public#42",
        operator_mark=pm.Level.SENSITIVE,
        prior_level=pm.Level.SENSITIVE,
        is_legacy_thread=True,
    )
    assert pm.resolve(inp, config=pm.DISABLED, policy=policy) is pm.Level.PUBLIC
    assert pm.resolve(inp, config=pm.DISABLED, policy=None) is pm.Level.PUBLIC


def test_unknown_repo_defaults_to_internal() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    policy = _sample_policy()
    inp = pm.ClassificationInput(repo="example-corp/unknown-repo")
    assert pm.resolve(inp, config=enabled, policy=policy) is pm.Level.INTERNAL
    assert pm.resolve(pm.ClassificationInput(), config=enabled, policy=policy) is pm.Level.INTERNAL


def test_known_repo_and_path_globs_max_join() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    policy = _sample_policy()
    pub_inp = pm.ClassificationInput(repo="example-corp/widgets-public", paths=("README.md",))
    assert pm.resolve(pub_inp, config=enabled, policy=policy) is pm.Level.PUBLIC

    elevated = pm.ClassificationInput(
        repo="example-corp/widgets-public",
        paths=("README.md", "secrets/prod.env"),
    )
    assert pm.resolve(elevated, config=enabled, policy=policy) is pm.Level.SENSITIVE


def test_operator_marks_join_via_policy_and_input() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    policy = _sample_policy()
    marked_target = pm.ClassificationInput(
        repo="example-corp/widgets-public",
        target_id="example-corp/widgets-public#42",
    )
    assert pm.resolve(marked_target, config=enabled, policy=policy) is pm.Level.SENSITIVE

    direct_mark = pm.ClassificationInput(
        repo="example-corp/widgets-public",
        operator_mark=pm.Level.INTERNAL,
    )
    assert pm.resolve(direct_mark, config=enabled, policy=policy) is pm.Level.INTERNAL


def test_legacy_threads_migrate_to_internal_never_public() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.PUBLIC)
    policy = _sample_policy()
    legacy = pm.ClassificationInput(
        repo="example-corp/widgets-public",
        is_legacy_thread=True,
    )
    assert pm.resolve(legacy, config=enabled, policy=policy) is pm.Level.INTERNAL


def test_downgrade_attempt_is_refused() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    policy = _sample_policy()
    downgrade = pm.ClassificationInput(
        repo="example-corp/widgets-public",
        prior_level=pm.Level.SENSITIVE,
    )
    with pytest.raises(pm.ClassificationError, match="downgrade"):
        pm.resolve(downgrade, config=enabled, policy=policy)

    downgrade_mark = pm.ClassificationInput(
        repo="example-corp/widgets-sensitive",
        operator_mark=pm.Level.PUBLIC,
    )
    with pytest.raises(pm.ClassificationError, match="downgrade"):
        pm.resolve(downgrade_mark, config=enabled, policy=policy)


def test_missing_or_invalid_policy_blocks_private_execution(tmp_path: Path) -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    with pytest.raises(pm.ClassificationError, match="policy is required"):
        pm.resolve(pm.ClassificationInput(repo="example-corp/widgets-public"), config=enabled, policy=None)

    sentinel = "SENTINEL-policy-secret"
    with pytest.raises(pm.ClassificationError) as exc:
        pm.parse_classification_policy({"default_level": sentinel, "repos": [], "path_globs": [], "operator_marks": {}})
    assert sentinel not in str(exc.value)

    with pytest.raises(pm.ClassificationError, match="does not exist") as exc2:
        pm.load_classification_policy(tmp_path / f"{sentinel}.json")
    assert sentinel not in str(exc2.value)

    with pytest.raises(pm.ClassificationError, match="policy_dir"):
        pm.load_overlay_classification_policy(None)

    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    (policy_dir / pm.POLICY_FILENAME).write_text(
        '{"default_level": "INTERNAL", "repos": [{"repo": "example-corp/widgets", "level": "SENSITIVE"}], '
        '"path_globs": [], "operator_marks": {}}',
        encoding="utf-8",
    )

    class _StubOverlay:
        policy_dir = "policy"

    loaded = pm.load_overlay_classification_policy(_StubOverlay(), base_dir=tmp_path)
    assert loaded.repos["example-corp/widgets"] is pm.Level.SENSITIVE


@pytest.mark.parametrize(
    ("bad_policy", "match"),
    [
        (["not-a-dict"], "top level must be an object"),
        ({"default_level": "INTERNAL"}, "missing required key"),
        (
            {"default_level": "INTERNAL", "repos": [], "path_globs": [], "operator_marks": {}, "extra": 1},
            "unknown key",
        ),
        (
            {"default_level": "PUBLIC", "repos": [], "path_globs": [], "operator_marks": {}},
            "at least INTERNAL",
        ),
        (
            {
                "default_level": "INTERNAL",
                "repos": [
                    {"repo": "example-corp/dup", "level": "PUBLIC"},
                    {"repo": "example-corp/dup", "level": "INTERNAL"},
                ],
                "path_globs": [],
                "operator_marks": {},
            },
            "duplicate repo",
        ),
        (
            {
                "default_level": "INTERNAL",
                "repos": [],
                "path_globs": [{"glob": "", "level": "SENSITIVE"}],
                "operator_marks": {},
            },
            "non-empty str",
        ),
    ],
)
def test_invalid_classification_policy_variants_fail_closed(bad_policy: object, match: str) -> None:
    with pytest.raises(pm.ClassificationError, match=match):
        pm.parse_classification_policy(bad_policy)


# --- PM-2 (#3252): immutable versioned envelope + propagation, taint, upgrade requeue, metric ---


def test_envelope_version_validation_and_monotonic_propagation() -> None:
    env = pm.stamp_envelope(
        pm.Level.INTERNAL, hop="dispatch", config=pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    )
    assert env.schema_version == pm.ENVELOPE_SCHEMA_VERSION
    assert env.level is pm.Level.INTERNAL
    assert env.epoch == 1
    assert env.hops == ("dispatch",)

    # Level never decreases along the chain (retry, sub_dispatch, fork_thread, artifact)
    retried = pm.propagate_envelope(env, hop="retry", requested_level=pm.Level.PUBLIC)
    assert retried.level is pm.Level.INTERNAL
    assert retried.hops == ("dispatch", "retry")

    upgraded = pm.propagate_envelope(retried, hop="sub_dispatch", requested_level=pm.Level.SENSITIVE)
    assert upgraded.level is pm.Level.SENSITIVE
    assert upgraded.epoch == retried.epoch + 1

    forked = pm.propagate_envelope(upgraded, hop="fork_thread", requested_level=pm.Level.INTERNAL)
    assert forked.level is pm.Level.SENSITIVE

    artifact = pm.propagate_envelope(forked, hop="artifact")
    assert artifact.level is pm.Level.SENSITIVE
    assert artifact.hops == ("dispatch", "retry", "sub_dispatch", "fork_thread", "artifact")

    # Round-trip and receiver validation
    wire = env.to_dict()
    restored = pm.validate_envelope(wire)
    assert restored == env

    bad_wire = dict(wire, schema_version=99)
    with pytest.raises(pm.ClassificationError, match="schema_version"):
        pm.validate_envelope(bad_wire)


def test_writer_context_taint_upgrades_internal_target_to_sensitive() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    writer_env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=enabled)
    with pm.envelope_context(writer_env):
        assert pm.current_envelope() == writer_env
        tainted_target = pm.taint_target_level(pm.Level.INTERNAL, config=enabled)
        assert tainted_target is pm.Level.SENSITIVE
        tainted_env = pm.taint_target_envelope(
            pm.stamp_envelope(pm.Level.INTERNAL, hop="thread", config=enabled),
            config=enabled,
        )
        assert tainted_env.level is pm.Level.SENSITIVE
        assert tainted_env.epoch == 2


def test_async_and_threadpool_context_preserved() -> None:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="parent", config=enabled)

    async def _child_coro() -> pm.ClassificationEnvelope | None:
        await asyncio.sleep(0)
        return pm.current_envelope()

    async def _main() -> tuple[pm.ClassificationEnvelope | None, pm.ClassificationEnvelope | None]:
        with pm.envelope_context(env):
            async_res = await asyncio.create_task(_child_coro())
            with ThreadPoolExecutor(max_workers=1) as pool:
                thread_res = pool.submit(pm.copy_context_callable(pm.current_envelope)).result()
            return async_res, thread_res

    async_env, thread_env = asyncio.run(_main())
    assert async_env == env
    assert thread_env == env


def test_mid_run_upgrade_triggers_checkpoint_requeue() -> None:
    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    initial = pm.stamp_envelope(pm.Level.INTERNAL, hop="run", config=enabled)

    same = pm.check_mid_run_upgrade(initial, live_level=pm.Level.INTERNAL, checkpoint_id="cp-1")
    assert same.requeue_required is False
    assert same.envelope == initial

    upgraded = pm.check_mid_run_upgrade(initial, live_level=pm.Level.SENSITIVE, checkpoint_id="cp-2")
    assert upgraded.requeue_required is True
    assert upgraded.checkpoint_id == "cp-2"
    assert upgraded.envelope.level is pm.Level.SENSITIVE
    assert upgraded.envelope.epoch == initial.epoch + 1

    with pytest.raises(pm.ClassificationError, match="downgrade"):
        pm.check_mid_run_upgrade(upgraded.envelope, live_level=pm.Level.INTERNAL, checkpoint_id="cp-3")


def test_flag_off_envelope_and_level_distribution_metric() -> None:
    pm.reset_level_distribution_counts()
    off_env = pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=pm.DISABLED)
    assert off_env.level is pm.Level.PUBLIC
    assert pm.taint_target_level(pm.Level.INTERNAL, config=pm.DISABLED) is pm.Level.PUBLIC

    enabled = pm.PrivateModeConfig(enabled=True, default_level=pm.Level.INTERNAL)
    pm.stamp_envelope(pm.Level.INTERNAL, hop="run", config=enabled)
    pm.stamp_envelope(pm.Level.SENSITIVE, hop="run", config=enabled)
    counts = pm.get_level_distribution_counts()
    assert counts[pm.Level.PUBLIC] >= 1
    assert counts[pm.Level.INTERNAL] == 1
    assert counts[pm.Level.SENSITIVE] == 1
