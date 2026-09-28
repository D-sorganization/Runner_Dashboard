"""Tests for webpush_crypto.py — RFC 8291 and RFC 8292 implementation."""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import webpush_crypto  # noqa: E402


def _b64url_decode(s: str) -> bytes:
    cleaned = s.replace("\n", "").replace(" ", "")
    rem = len(cleaned) % 4
    if rem:
        cleaned += "=" * (4 - rem)
    return base64.urlsafe_b64decode(cleaned)


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def test_rfc8291_appendix_a_test_vector() -> None:
    """Verify byte-for-byte exactness against RFC 8291 Appendix A test vector."""
    # The key material below is the public RFC 8291 Appendix A vector, not a secret.
    as_private_b64 = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"  # noqa: E501  # pragma: allowlist secret
    ua_public_b64 = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"  # noqa: E501  # pragma: allowlist secret
    salt_b64 = "DGv6ra1nlYgDCS1FRnbzlw"
    auth_secret_b64 = "BTBZMqHH6r4Tts7J_aSIgg"  # noqa: E501  # pragma: allowlist secret  # gitleaks:allow
    plaintext_b64 = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"  # noqa: E501  # pragma: allowlist secret

    plaintext = _b64url_decode(plaintext_b64)
    salt = _b64url_decode(salt_b64)
    as_private = webpush_crypto.load_vapid_private_key(as_private_b64)

    body, headers = webpush_crypto.encrypt_webpush_payload(
        plaintext=plaintext,
        p256dh=ua_public_b64,
        auth=auth_secret_b64,
        salt=salt,
        as_private=as_private,
    )

    assert headers["Content-Encoding"] == "aes128gcm"

    # RFC 8291 Appendix A intermediate checks
    expected_header_b64 = (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml"  # noqa: E501  # pragma: allowlist secret
        "mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"  # noqa: E501  # pragma: allowlist secret
    )
    expected_ciphertext_b64 = "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ"  # noqa: E501  # pragma: allowlist secret

    header = body[:86]
    ciphertext = body[86:]

    assert _b64url_encode(header) == expected_header_b64
    assert _b64url_encode(ciphertext) == expected_ciphertext_b64
    assert len(body) == 86 + len(_b64url_decode(expected_ciphertext_b64))


def test_vapid_keygen_produces_valid_keypair() -> None:
    """Generate VAPID keypair and verify keys are valid P-256 keys."""
    priv_b64, pub_b64 = webpush_crypto.generate_vapid_keypair()

    priv_key = webpush_crypto.load_vapid_private_key(priv_b64)
    pub_key = webpush_crypto.load_vapid_public_key(pub_b64)

    assert priv_key is not None
    assert pub_key is not None

    exported_pub = webpush_crypto.export_vapid_public_key(pub_key)
    assert exported_pub == pub_b64

    # Ensure uncompressed point length (65 bytes -> ~87 chars in base64url without padding)
    raw_pub = _b64url_decode(pub_b64)
    assert len(raw_pub) == 65
    assert raw_pub[0] == 0x04


def test_vapid_jwt_header_claims_and_signature_verification() -> None:
    """Verify VAPID JWT matches RFC 8292 specifications."""
    priv_b64, pub_b64 = webpush_crypto.generate_vapid_keypair()
    priv_key = webpush_crypto.load_vapid_private_key(priv_b64)
    pub_key = webpush_crypto.load_vapid_public_key(pub_b64)

    endpoint = "https://updates.push.services.mozilla.com/wpush/v2/gAAAAABj..."
    subject = "mailto:operator@example.com"
    ttl = 43200

    headers = webpush_crypto.create_vapid_auth_header(
        endpoint=endpoint,
        subject=subject,
        private_key=priv_key,
        public_key=pub_key,
        ttl=ttl,
        urgency="high",
    )

    assert headers["TTL"] == "43200"
    assert headers["Urgency"] == "high"
    assert "Authorization" in headers

    auth_val = headers["Authorization"]
    assert auth_val.startswith("vapid ")
    # Format: vapid t=..., k=...
    parts = auth_val[6:].split(", ")
    kv = dict(part.split("=", 1) for part in parts)
    jwt_token = kv["t"]
    key_param = kv["k"]

    assert key_param == pub_b64

    # Parse JWT parts
    jwt_parts = jwt_token.split(".")
    assert len(jwt_parts) == 3

    header_bytes = _b64url_decode(jwt_parts[0])
    claims_bytes = _b64url_decode(jwt_parts[1])

    jwt_header = json.loads(header_bytes.decode("utf-8"))
    jwt_claims = json.loads(claims_bytes.decode("utf-8"))

    assert jwt_header["typ"] == "JWT"
    assert jwt_header["alg"] == "ES256"

    assert jwt_claims["aud"] == "https://updates.push.services.mozilla.com"
    assert jwt_claims["sub"] == subject
    now = int(time.time())
    assert now <= jwt_claims["exp"] <= now + 86400  # exp <= 24h

    # Verify signature using public key
    valid = webpush_crypto.verify_vapid_jwt(jwt_token, pub_key)
    assert valid is True


def test_load_vapid_private_key_from_pem(tmp_path: Path) -> None:
    """Verify loading private key from PEM file path."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem_file = tmp_path / "vapid_private.pem"
    pem_file.write_bytes(pem_bytes)

    loaded = webpush_crypto.load_vapid_private_key(str(pem_file))
    assert loaded is not None
    assert loaded.curve.name == "secp256r1"
