"""Render every diagram source in docs/diagrams/ to SVG and PNG through Kroki.

    python docs/diagrams/render.py              # all *.puml and *.mmd
    python docs/diagrams/render.py use-cases    # only sources whose name contains "use-cases"

Standard library only. Sources are POSTed as text/plain to https://kroki.io/<kind>/<format>
(kind = plantuml for .puml, mermaid for .mmd). If Kroki fails for a PlantUML source, the output
is fetched from the public PlantUML server instead (www.plantuml.com, deflate + PlantUML base64).
Mermaid output is transparent, so its SVGs get a white background and its PNGs are flattened
onto white (readable in dark mode and in any image viewer). Each output is checked (SVG parses
as XML with an <svg> root, PNG starts with the PNG signature and ends with IEND) before it
replaces the old file, so a failed render never leaves a broken export behind. The script exits
1 if anything failed.

The diagram sources hold no personal data or secrets, so sending them to these services is fine.
"""

from __future__ import annotations

import re
import struct
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
KROKI = "https://kroki.io"
PLANTUML_SERVER = "https://www.plantuml.com/plantuml"
KINDS = {".puml": "plantuml", ".mmd": "mermaid"}
FORMATS = ("svg", "png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TIMEOUT_S = 90
ATTEMPTS = 3
USER_AGENT = "docusense-diagrams/1.0 (+render.py)"


def _request(url: str, data: bytes | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "text/plain; charset=utf-8"
    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()[:500].decode("utf-8", "replace")
            # 4xx = the source is wrong; retrying will not help.
            if 400 <= exc.code < 500:
                raise RuntimeError(f"HTTP {exc.code} from {url[:80]}: {body}") from exc
            last = RuntimeError(f"HTTP {exc.code}: {body}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url[:80]}: {last}")


# PlantUML's text encoding: raw deflate, then base64 with its own alphabet.
_PUML_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-_"


def plantuml_encode(text: str) -> str:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    data = compressor.compress(text.encode("utf-8")) + compressor.flush()
    out = []
    for i in range(0, len(data), 3):
        chunk = data[i:i + 3]
        b1, b2, b3 = (chunk + b"\0\0")[:3]
        n = (b1 << 16) | (b2 << 8) | b3
        out.append("".join(_PUML_ALPHABET[(n >> s) & 0x3F] for s in (18, 12, 6, 0)))
    return "".join(out)


def check_svg(data: bytes) -> None:
    root = ET.fromstring(data)  # raises ParseError on broken XML
    if not root.tag.endswith("svg"):
        raise ValueError(f"root element is <{root.tag}>, not <svg>")
    if b"Syntax Error" in data or b"Syntax error" in data:
        raise ValueError("the renderer drew a syntax error instead of the diagram")


def check_png(data: bytes) -> None:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("missing PNG signature")
    if b"IEND" not in data[-16:]:
        raise ValueError("PNG is truncated (no IEND chunk)")


def _chunk(kind: bytes, body: bytes) -> bytes:
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def flatten_png(data: bytes) -> bytes:
    """Composite an 8-bit RGBA PNG (Mermaid's are transparent) onto white, as RGB.

    A transparent export turns black in many image viewers and in dark mode, which hides the
    black lines; PlantUML's PNGs are already opaque and pass through unchanged.
    """
    pos, chunks = 8, []
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        chunks.append((data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]))
        pos += 12 + length
    header = chunks[0][1]
    width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", header)
    if (depth, colour, interlace) != (8, 6, 0):
        return data
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    bpp, stride = 4, width * 4
    prev = bytearray(stride)
    out = bytearray()
    for y in range(height):
        start = y * (stride + 1)
        kind, row = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        if kind == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif kind == 2:
            for i in range(stride):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif kind == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b, c = prev[i], prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[i] = (row[i] + pred) & 0xFF
        elif kind != 0:
            raise ValueError(f"PNG row filter {kind}")
        prev = row
        rgb = bytearray(width * 3)
        for x in range(width):
            r, g, b_, a = row[4 * x:4 * x + 4]
            rgb[3 * x] = (r * a + 255 * (255 - a)) // 255
            rgb[3 * x + 1] = (g * a + 255 * (255 - a)) // 255
            rgb[3 * x + 2] = (b_ * a + 255 * (255 - a)) // 255
        out += b"\x00" + rgb
    new_header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (PNG_SIGNATURE + _chunk(b"IHDR", new_header)
            + _chunk(b"IDAT", zlib.compress(bytes(out), 9)) + _chunk(b"IEND", b""))


def white_svg(data: bytes) -> bytes:
    """Give a Mermaid SVG a white background (it is transparent: unreadable in dark mode)."""
    text = data.decode("utf-8")
    match = re.search(r"<svg\b[^>]*>", text)
    if match is None or "background-color" in match.group(0):
        return data
    tag = match.group(0)
    if ' style="' in tag:
        new = tag.replace(' style="', ' style="background-color: white; ', 1)
    else:
        new = tag[:-1] + ' style="background-color: white;">'
    return (text[:match.start()] + new + text[match.end():]).encode("utf-8")


def render(source: Path, fmt: str) -> tuple[bytes, str]:
    kind = KINDS[source.suffix]
    text = source.read_text(encoding="utf-8")
    try:
        return _request(f"{KROKI}/{kind}/{fmt}", text.encode("utf-8")), "kroki"
    except RuntimeError as exc:
        if kind != "plantuml":
            raise
        print(f"  kroki failed ({exc}); trying {PLANTUML_SERVER}", file=sys.stderr)
        return _request(f"{PLANTUML_SERVER}/{fmt}/{plantuml_encode(text)}"), "plantuml.com"


def main(filters: list[str]) -> int:
    sources = sorted(p for p in HERE.iterdir() if p.suffix in KINDS)
    if filters:
        sources = [p for p in sources if any(f in p.name for f in filters)]
    if not sources:
        print("no diagram sources matched", file=sys.stderr)
        return 1
    failures = []
    for source in sources:
        for fmt in FORMATS:
            target = source.with_suffix(f".{fmt}")
            try:
                data, via = render(source, fmt)
                (check_svg if fmt == "svg" else check_png)(data)
                if KINDS[source.suffix] == "mermaid":
                    data = white_svg(data) if fmt == "svg" else flatten_png(data)
                    (check_svg if fmt == "svg" else check_png)(data)
            except Exception as exc:  # noqa: BLE001 - report every failure, keep going
                failures.append(f"{target.name}: {exc}")
                print(f"FAILED {target.name}: {exc}", file=sys.stderr)
                continue
            target.write_bytes(data)
            print(f"wrote {target.name} ({len(data):,} bytes via {via})")
    if failures:
        print(f"\n{len(failures)} export(s) failed; old files left untouched.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
