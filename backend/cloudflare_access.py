"""Validate human Cloudflare Access JWT assertions without trusting request claims."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt

_CERTS_PATH = "/cdn-cgi/access/certs"
_MAX_TOKEN_LENGTH = 16_384
_MAX_JWKS_BYTES = 256 * 1024
_UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 30.0


class CloudflareAccessValidationError(ValueError):
    """A Cloudflare Access assertion could not be verified."""


class CloudflareAccessValidator:
    """Verify Access JWTs using only a configured issuer and audience."""

    def __init__(
        self,
        *,
        issuer: str | None,
        audience: str | None,
        enabled: bool = False,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 3.0,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        self._issuer = issuer
        self._audience = audience.strip() if isinstance(audience, str) else ""
        self._enabled = enabled
        self._owns_http_client = http_client is None
        self._timeout_seconds = timeout_seconds
        self._cache_ttl_seconds = cache_ttl_seconds
        self._http_client = http_client if http_client is not None else httpx.Client(follow_redirects=False)
        self._jwks_url = f"{issuer}{_CERTS_PATH}" if _valid_issuer(issuer) else ""
        self._keys: dict[str, Any] = {}
        self._keys_expires_at = 0.0
        self._keys_fetched_at = 0.0
        self._cache_lock = threading.Lock()

    def validate(self, token: str) -> Mapping[str, Any]:
        """Return claims; blocking callers in an ASGI loop must use asyncio.to_thread."""
        if not self._configuration_is_valid():
            raise CloudflareAccessValidationError("Cloudflare Access JWT validation is unavailable")
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
            raise CloudflareAccessValidationError("Cloudflare Access JWT validation failed")

        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise CloudflareAccessValidationError("Cloudflare Access JWT validation failed")
            key = self._get_signing_key(header["kid"])
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["iss", "aud", "exp", "nbf", "sub"]},
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject.strip() or "common_name" in claims:
                raise CloudflareAccessValidationError("Cloudflare Access JWT validation failed")
            return claims
        except CloudflareAccessValidationError:
            raise
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise CloudflareAccessValidationError("Cloudflare Access JWT validation failed") from None

    def close(self) -> None:
        """Close the internally created HTTP client, if this validator owns it."""
        if self._owns_http_client:
            self._http_client.close()

    def _configuration_is_valid(self) -> bool:
        return bool(
            self._enabled is True
            and self._jwks_url
            and self._audience
            and _valid_bound(self._timeout_seconds, maximum=10)
            and _valid_bound(self._cache_ttl_seconds, maximum=3600)
        )

    def _get_signing_key(self, kid: str) -> Any:
        now = time.monotonic()
        with self._cache_lock:
            if now >= self._keys_expires_at:
                self._refresh_keys()
            key = self._keys.get(kid)
            if key is None and now - self._keys_fetched_at >= _UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS:
                self._refresh_keys()
                key = self._keys.get(kid)
            if key is None:
                raise CloudflareAccessValidationError("Cloudflare Access JWT validation failed")
            return key

    def _refresh_keys(self) -> None:
        with self._http_client.stream(
            "GET",
            self._jwks_url,
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200 or str(response.url) != self._jwks_url:
                raise CloudflareAccessValidationError("Cloudflare Access signing keys are unavailable")
            content_length = response.headers.get("content-length")
            if content_length is not None and int(content_length) > _MAX_JWKS_BYTES:
                raise CloudflareAccessValidationError("Cloudflare Access signing keys are unavailable")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_JWKS_BYTES:
                    raise CloudflareAccessValidationError("Cloudflare Access signing keys are unavailable")
        payload = json.loads(body)
        raw_keys = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(raw_keys, list):
            raise CloudflareAccessValidationError("Cloudflare Access signing keys are unavailable")
        keys: dict[str, Any] = {}
        for item in raw_keys:
            if not isinstance(item, dict):
                continue
            kid = item.get("kid")
            if (
                not isinstance(kid, str)
                or not kid
                or item.get("kty") != "RSA"
                or item.get("alg") != "RS256"
                or item.get("use", "sig") != "sig"
            ):
                continue
            try:
                keys[kid] = jwt.PyJWK.from_dict(item, algorithm="RS256").key
            except (jwt.PyJWTError, ValueError, TypeError):
                continue
        if not keys:
            raise CloudflareAccessValidationError("Cloudflare Access signing keys are unavailable")
        self._keys = keys
        self._keys_fetched_at = time.monotonic()
        self._keys_expires_at = self._keys_fetched_at + self._cache_ttl_seconds


def _valid_issuer(issuer: str | None) -> bool:
    if not isinstance(issuer, str) or not issuer or issuer != issuer.strip():
        return False
    try:
        parts = urlsplit(issuer)
        host = parts.hostname
    except ValueError:
        return False
    suffix = ".cloudflareaccess.com"
    if (
        parts.scheme != "https"
        or host is None
        or not host.endswith(suffix)
        or parts.netloc != host
        or parts.path
        or parts.query
        or parts.fragment
    ):
        return False
    team = host[: -len(suffix)]
    return (
        bool(team)
        and "." not in team
        and all(char.isascii() and (char.isalnum() or char == "-") for char in team)
        and team[0].isalnum()
        and team[-1].isalnum()
    )


def _valid_bound(value: object, *, maximum: float) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and 0 < value <= maximum
    except OverflowError:
        return False
