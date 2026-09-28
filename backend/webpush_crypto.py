"""Cryptographic primitives for Web Push (RFC 8291) and VAPID (RFC 8292).

Provides:
- AES-128-GCM message encryption per RFC 8291
- VAPID key management and ES256 JWT creation per RFC 8292
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

log = logging.getLogger(__name__)

RECORD_SIZE: int = 4096
VAPID_JWT_LIFETIME_S: int = 12 * 3600


def b64url_decode(s: str) -> bytes:
    """Decode a base64url string, handling missing padding and whitespace."""
    cleaned = s.strip().replace("\n", "").replace(" ", "")
    rem = len(cleaned) % 4
    if rem:
        cleaned += "=" * (4 - rem)
    return base64.urlsafe_b64decode(cleaned)


def b64url_encode(b: bytes) -> str:
    """Encode bytes to an unpadded base64url string."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def load_vapid_private_key(raw_or_path: str) -> ec.EllipticCurvePrivateKey:
    """Load a P-256 private key from a raw 32-byte scalar (base64url) or PEM path/content."""
    raw_str = raw_or_path.strip()
    if os.path.exists(raw_str):
        pem_bytes = Path(raw_str).read_bytes()
        loaded = serialization.load_pem_private_key(pem_bytes, password=None)
        if not isinstance(loaded, ec.EllipticCurvePrivateKey):
            raise ValueError("PEM file does not contain an EC private key")
        return loaded

    if raw_str.startswith("-----BEGIN"):
        loaded = serialization.load_pem_private_key(raw_str.encode("utf-8"), password=None)
        if not isinstance(loaded, ec.EllipticCurvePrivateKey):
            raise ValueError("PEM data does not contain an EC private key")
        return loaded

    raw_bytes = b64url_decode(raw_str)
    if len(raw_bytes) != 32:
        raise ValueError(f"Expected 32-byte scalar for P-256 private key, got {len(raw_bytes)}")
    scalar = int.from_bytes(raw_bytes, "big")
    return ec.derive_private_key(scalar, ec.SECP256R1())


def load_vapid_public_key(raw_or_bytes: str | bytes) -> ec.EllipticCurvePublicKey:
    """Load a P-256 public key from uncompressed point bytes (0x04 || X || Y) or base64url."""
    raw_bytes = b64url_decode(raw_or_bytes) if isinstance(raw_or_bytes, str) else raw_or_bytes
    if len(raw_bytes) != 65 or raw_bytes[0] != 0x04:
        raise ValueError(f"Expected 65-byte uncompressed point (0x04...), got {len(raw_bytes)} bytes")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw_bytes)


def export_vapid_public_key(key: ec.EllipticCurvePublicKey) -> str:
    """Export public key as base64url-encoded uncompressed point (65 bytes)."""
    raw_bytes = key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return b64url_encode(raw_bytes)


def export_vapid_private_key(key: ec.EllipticCurvePrivateKey) -> str:
    """Export private key scalar as base64url-encoded 32 bytes."""
    val = key.private_numbers().private_value
    return b64url_encode(val.to_bytes(32, "big"))


def generate_vapid_keypair() -> tuple[str, str]:
    """Generate a new P-256 keypair returning (private_b64url, public_b64url)."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key()
    return export_vapid_private_key(priv), export_vapid_public_key(pub)


def encrypt_webpush_payload(
    plaintext: bytes,
    p256dh: str | bytes,
    auth: str | bytes,
    *,
    salt: bytes | None = None,
    as_private: ec.EllipticCurvePrivateKey | None = None,
) -> tuple[bytes, dict[str, str]]:
    """Encrypt plaintext using RFC 8291 (aes128gcm).

    Returns (body_bytes, headers_dict).
    """
    ua_public_bytes = b64url_decode(p256dh) if isinstance(p256dh, str) else p256dh
    ua_public = load_vapid_public_key(ua_public_bytes)

    auth_secret = b64url_decode(auth) if isinstance(auth, str) else auth
    if len(auth_secret) != 16:
        raise ValueError(f"Authentication secret must be 16 bytes, got {len(auth_secret)}")

    if as_private is None:
        as_private = ec.generate_private_key(ec.SECP256R1())

    as_public = as_private.public_key()
    as_public_bytes = as_public.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    if salt is None:
        salt = os.urandom(16)
    elif len(salt) != 16:
        raise ValueError(f"Salt must be 16 bytes, got {len(salt)}")

    # 1. ECDH shared secret
    ecdh_secret = as_private.exchange(ec.ECDH(), ua_public)

    # 2. Key combining (RFC 8291 Section 3.4)
    prk_key = hmac.new(auth_secret, ecdh_secret, hashlib.sha256).digest()
    key_info = b"WebPush: info\x00" + ua_public_bytes + as_public_bytes
    ikm = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=key_info).derive(prk_key)

    # 3. Content encryption key and nonce (RFC 8188 Section 2.2)
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    cek_info = b"Content-Encoding: aes128gcm\x00"
    cek = HKDFExpand(algorithm=hashes.SHA256(), length=16, info=cek_info).derive(prk)

    nonce_info = b"Content-Encoding: nonce\x00"
    nonce = HKDFExpand(algorithm=hashes.SHA256(), length=12, info=nonce_info).derive(prk)

    # 4. Binary header (RFC 8188 Section 2.1):
    #    salt (16 octets) + rs (4 octets BE) + idlen (1 octet) + keyid (idlen octets)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + len(as_public_bytes).to_bytes(1, "big") + as_public_bytes

    # 5. Encrypt plaintext + delimiter octet (0x02 for single/final record)
    padded_plaintext = plaintext + b"\x02"
    aesgcm = AESGCM(cek)
    ciphertext = aesgcm.encrypt(nonce, padded_plaintext, None)

    return header + ciphertext, {"Content-Encoding": "aes128gcm"}


def create_vapid_auth_header(
    endpoint: str,
    subject: str,
    private_key: ec.EllipticCurvePrivateKey,
    public_key: ec.EllipticCurvePublicKey,
    ttl: int = 86400,
    urgency: str = "normal",
) -> dict[str, str]:
    """Create VAPID Authorization, TTL, and Urgency headers (RFC 8292)."""
    parsed = urllib.parse.urlsplit(endpoint)
    audience = f"{parsed.scheme}://{parsed.netloc}"

    # RFC 8292 caps exp at 24h; 12h leaves margin for clock skew (push services reject >24h).
    exp = int(time.time()) + VAPID_JWT_LIFETIME_S
    claims = {
        "aud": audience,
        "exp": exp,
        "sub": subject,
    }

    header = {"typ": "JWT", "alg": "ES256"}
    header_json = json.dumps(header, separators=(",", ":")).encode("utf-8")
    claims_json = json.dumps(claims, separators=(",", ":")).encode("utf-8")

    header_b64 = b64url_encode(header_json)
    claims_b64 = b64url_encode(claims_json)
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")

    der_sig = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    sig_b64 = b64url_encode(raw_sig)

    jwt_token = f"{header_b64}.{claims_b64}.{sig_b64}"
    pub_key_b64 = export_vapid_public_key(public_key)

    return {
        "Authorization": f"vapid t={jwt_token}, k={pub_key_b64}",
        "TTL": str(ttl),
        "Urgency": urgency,
    }


def verify_vapid_jwt(token: str, public_key: ec.EllipticCurvePublicKey) -> bool:
    """Verify an ES256 VAPID JWT signature against the given public key."""
    parts = token.split(".")
    if len(parts) != 3:
        return False
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    raw_sig = b64url_decode(parts[2])
    if len(raw_sig) != 64:
        return False
    r = int.from_bytes(raw_sig[:32], "big")
    s = int.from_bytes(raw_sig[32:], "big")
    der_sig = encode_dss_signature(r, s)
    try:
        public_key.verify(der_sig, signing_input, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False
