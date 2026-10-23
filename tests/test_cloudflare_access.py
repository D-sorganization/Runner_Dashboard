from __future__ import annotations

import time
from typing import Any

import cloudflare_access
import httpx
import jwt
import pytest
from cloudflare_access import CloudflareAccessValidationError, CloudflareAccessValidator
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://team.cloudflareaccess.com"
AUDIENCE = "runner-dashboard-audience"
JWKS_URL = f"{ISSUER}/cdn-cgi/access/certs"


@pytest.fixture
def signing_pair() -> tuple[Any, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


def make_token(private_key: Any, *, kid: str = "test-key", claims: dict[str, Any] | None = None) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": [AUDIENCE],
        "exp": now + 60,
        "nbf": now - 1,
        "sub": "human-subject",
        "email": "owner@example.test",
    }
    for key, value in (claims or {}).items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


def validator_for(signing_pair, *, response_keys=None, responses=None, **kwargs):
    private_key, public_jwk = signing_pair
    calls: list[httpx.Request] = []
    payload = {"keys": response_keys if response_keys is not None else [public_jwk]}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if responses:
            return responses.pop(0)
        return httpx.Response(200, json=payload, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    validator = CloudflareAccessValidator(issuer=ISSUER, audience=AUDIENCE, http_client=client, **kwargs)
    return validator, private_key, calls


def test_valid_human_token_is_verified_against_fixed_cloudflare_keys(signing_pair) -> None:
    validator, private_key, calls = validator_for(signing_pair)

    claims = validator.validate(make_token(private_key))

    assert claims["sub"] == "human-subject"
    assert calls[0].url == JWKS_URL


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://attacker.cloudflareaccess.com"},
        {"aud": ["different-audience"]},
        {"exp": int(time.time()) - 1},
        {"nbf": int(time.time()) + 120},
        {"iss": None},
        {"aud": None},
        {"exp": None},
        {"nbf": None},
        {"sub": None},
        {"sub": ""},
        {"sub": "human-subject", "common_name": "service-token.access"},
    ],
)
def test_invalid_claims_fail_closed(signing_pair, claims) -> None:
    validator, private_key, _calls = validator_for(signing_pair)

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate(make_token(private_key, claims=claims))


def test_bad_signature_fails_closed(signing_pair) -> None:
    validator, private_key, _calls = validator_for(signing_pair)
    token = make_token(private_key)
    altered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate(altered)


def test_unknown_key_fails_closed_without_token_controlled_fetch(signing_pair) -> None:
    validator, private_key, calls = validator_for(signing_pair)

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate(make_token(private_key, kid="unknown-key"))

    assert len(calls) == 1
    assert calls[0].url == JWKS_URL


def test_cached_key_set_avoids_repeat_network_requests(signing_pair) -> None:
    validator, private_key, calls = validator_for(signing_pair)
    token = make_token(private_key)

    validator.validate(token)
    validator.validate(token)

    assert len(calls) == 1


def test_expired_key_cache_is_refreshed(signing_pair, monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(cloudflare_access.time, "monotonic", lambda: now[0])
    validator, private_key, calls = validator_for(signing_pair, cache_ttl_seconds=30)
    token = make_token(private_key)

    validator.validate(token)
    now[0] = 131.0
    validator.validate(token)

    assert len(calls) == 2


@pytest.mark.parametrize(
    "issuer,audience,enabled",
    [
        (None, AUDIENCE, True),
        (ISSUER, None, True),
        ("http://team.cloudflareaccess.com", AUDIENCE, True),
        ("https://cloudflareaccess.com", AUDIENCE, True),
        ("https://team.cloudflareaccess.com.evil.test", AUDIENCE, True),
        ("https://team.cloudflareaccess.com/path", AUDIENCE, True),
        (ISSUER, AUDIENCE, False),
    ],
)
def test_disabled_or_invalid_configuration_fails_before_network(issuer, audience, enabled) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"keys": []}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validator = CloudflareAccessValidator(issuer=issuer, audience=audience, enabled=enabled, http_client=client)

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate("not-a-jwt")

    assert calls == []


def test_bounded_http_timeout_and_cache_ttl_are_enforced(signing_pair) -> None:
    validator, private_key, calls = validator_for(signing_pair, timeout_seconds=2.5, cache_ttl_seconds=30)
    original_stream = validator._http_client.stream
    observed: list[dict[str, Any]] = []

    def recording_stream(method, url, **kwargs):
        observed.append({"method": method, "url": str(url), **kwargs})
        return original_stream(method, url, **kwargs)

    validator._http_client.stream = recording_stream
    validator.validate(make_token(private_key))

    assert observed[0]["method"] == "GET"
    assert observed[0]["url"] == JWKS_URL
    assert observed[0]["timeout"] == 2.5
    assert observed[0]["follow_redirects"] is False
    assert validator._cache_ttl_seconds == 30
    assert len(calls) == 1


def test_non_rs256_algorithm_is_rejected_before_key_fetch(signing_pair) -> None:
    validator, _private_key, calls = validator_for(signing_pair)
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": now + 60, "nbf": now - 1, "sub": "human"},
        "not-an-rsa-key-that-is-long-enough-for-hmac-sha256",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate(token)

    assert calls == []


def test_redirected_or_malformed_key_response_fails_closed(signing_pair) -> None:
    validator, private_key, _calls = validator_for(
        signing_pair,
        responses=[httpx.Response(302, headers={"Location": "https://attacker.example/keys"})],
    )

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate(make_token(private_key))

    wrong_key = {"kid": "wrong-alg", "kty": "RSA", "alg": "RS512"}
    malformed, private_key, _calls = validator_for(signing_pair, response_keys=[wrong_key])
    with pytest.raises(CloudflareAccessValidationError):
        malformed.validate(make_token(private_key))


def test_oversized_key_response_is_rejected_before_unbounded_read(signing_pair) -> None:
    validator, private_key, _calls = validator_for(
        signing_pair, responses=[httpx.Response(200, headers={"content-length": "262145"})]
    )

    with pytest.raises(CloudflareAccessValidationError):
        validator.validate(make_token(private_key))


def test_out_of_range_timeout_or_cache_fails_without_network(signing_pair) -> None:
    for values in ({"timeout_seconds": 11}, {"cache_ttl_seconds": 3601}, {"timeout_seconds": 10**1000}):
        validator, private_key, calls = validator_for(signing_pair, **values)
        with pytest.raises(CloudflareAccessValidationError):
            validator.validate(make_token(private_key))
        assert calls == []
