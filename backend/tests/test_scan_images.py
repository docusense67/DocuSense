"""Scan page compression (app/services/scan_images.py)."""

import io
import struct
import zlib

import pytest
from PIL import Image, ImageDraw

from app.services.scan_images import InvalidScanImage, compress_page

OPTIONS = {"max_width": 1654, "quality": 70}


def encode(image: Image.Image, fmt: str) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt)
    return out.getvalue()


def a4(width: int, mode: str = "L") -> Image.Image:
    page = Image.new(mode, (width, round(width * 297 / 210)), "white")
    draw = ImageDraw.Draw(page)
    for row in range(30):
        draw.text((width // 10, 200 + row * 80), f"GE{100 + row} Purposive Communication 1.75",
                  fill="black", font_size=width // 60)
    return page


@pytest.mark.parametrize(("fmt", "mode"), [("PNG", "L"), ("PNG", "RGB"), ("JPEG", "RGB")])
def test_large_pages_become_grayscale_jpeg_at_200_dpi(fmt, mode):
    upload = encode(a4(2480, mode), fmt)  # 300 dpi A4
    data, ext = compress_page(upload, **OPTIONS)
    assert ext == "jpg"
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG" and image.mode == "L"
        # same aspect ratio, so the review screen's mm-based crops land on the same text
        assert image.size == (1654, 2339)


def test_small_upload_is_kept_as_is():
    upload = encode(Image.new("1", (800, 1131), 1), "PNG")  # bilevel PNG: already tiny
    assert compress_page(upload, **OPTIONS) == (upload, "png")


def test_narrow_noisy_page_is_recompressed():
    noisy = Image.effect_noise((1200, 1697), 60)
    upload = encode(noisy, "PNG")
    data, ext = compress_page(upload, **OPTIONS)
    assert ext == "jpg" and len(data) < len(upload)
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (1200, 1697)  # never upscaled


def _png_header(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk
            + struct.pack(">I", zlib.crc32(chunk)))


@pytest.mark.parametrize("content", [
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,  # magic bytes only
    encode(a4(1000), "PNG")[:5000],  # truncated
    _png_header(40_000, 40_000),  # 1.6 Gpx decompression bomb, refused before decoding
    encode(Image.new("L", (10, 10)), "GIF"),
])
def test_unreadable_or_oversized_images_are_refused(content):
    with pytest.raises(InvalidScanImage):
        compress_page(content, **OPTIONS)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_truncated_upload_is_refused_even_when_pillow_tolerates_truncation(monkeypatch, fmt):
    # WeasyPrint sets this flag globally on import (production API on Linux).
    from PIL import ImageFile
    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    whole = encode(a4(1000), fmt)
    with pytest.raises(InvalidScanImage):
        compress_page(whole[: len(whole) // 2], **OPTIONS)
    compress_page(whole, **OPTIONS)  # the complete file is still accepted
