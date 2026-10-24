"""Canonical manifest metadata remains explicit, typed and backwards compatible."""

import pytest
from knowledge_pack.manifest import ManifestError, manifest_from_dict

pytestmark = pytest.mark.unit


def manifest(**metadata: object) -> dict[str, object]:
    return {
        "id": "projects",
        "sources": [{"repo": "UpstreamDrift", "authority": "product", "include": ["SPEC.md"]}],
        **metadata,
    }


@pytest.mark.parametrize("enabled", [False, True])
def test_optional_embedding_metadata_round_trips(enabled: bool) -> None:
    parsed = manifest_from_dict(manifest(embeddings=enabled))
    assert parsed.embeddings is enabled
    assert parsed.to_dict()["embeddings"] is enabled
    assert manifest_from_dict(parsed.to_dict()) == parsed


def test_legacy_manifest_defaults_to_no_embeddings() -> None:
    parsed = manifest_from_dict(manifest())
    assert parsed.embeddings is False
    assert manifest_from_dict(parsed.to_dict()) == parsed


@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_embedding_metadata_requires_a_boolean(value: object) -> None:
    with pytest.raises(ManifestError, match="embeddings must be a boolean"):
        manifest_from_dict(manifest(embeddings=value))
