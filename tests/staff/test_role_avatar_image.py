"""Unit tests for avatar upload sniffing and dimension parsing (#2680)."""

from __future__ import annotations

import struct
import zlib

import pytest
from staff.role_avatar_image import AvatarValidationError, parse_dimensions, sniff_image_kind, validate_avatar_upload


def _png_with_dims(width: int, height: int, extra: bytes = b"\x00" * 64) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    idat = zlib.compress(extra)
    idat_chunk = struct.pack(">I", len(idat)) + b"IDAT" + idat + struct.pack(">I", zlib.crc32(b"IDAT" + idat) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return sig + chunk + idat_chunk + iend


@pytest.mark.unit
def test_sniff_rejects_svg_and_gif() -> None:
    assert sniff_image_kind(b"<svg></svg>") is None
    assert sniff_image_kind(b"GIF89a") is None


@pytest.mark.unit
def test_validate_accepts_128_square_png() -> None:
    parsed = validate_avatar_upload(_png_with_dims(128, 128))
    assert parsed.width == 128
    assert parsed.height == 128
    assert parsed.ext == "png"


@pytest.mark.unit
def test_validate_rejects_oversize_payload() -> None:
    from staff.role_avatars import MAX_AVATAR_BYTES

    with pytest.raises(AvatarValidationError) as exc:
        validate_avatar_upload(b"\x00" * (MAX_AVATAR_BYTES + 1))
    assert exc.value.status == 413


@pytest.mark.unit
def test_validate_rejects_small_and_wide_images() -> None:
    with pytest.raises(AvatarValidationError) as exc:
        validate_avatar_upload(_png_with_dims(32, 32))
    assert exc.value.status == 422
    with pytest.raises(AvatarValidationError) as exc:
        validate_avatar_upload(_png_with_dims(5000, 5000))
    assert exc.value.status == 422
    with pytest.raises(AvatarValidationError) as exc:
        validate_avatar_upload(_png_with_dims(400, 100))
    assert exc.value.status == 422


@pytest.mark.unit
def test_validate_rejects_truncated_png_header() -> None:
    with pytest.raises(AvatarValidationError) as exc:
        validate_avatar_upload(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert exc.value.status == 422


@pytest.mark.unit
def test_parse_dimensions_png() -> None:
    data = _png_with_dims(200, 150)
    assert parse_dimensions(data, "png") == (200, 150)
