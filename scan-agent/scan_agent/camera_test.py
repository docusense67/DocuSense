"""Capture one page from the camera and write what the station would submit.

    python -m scan_agent camera-test                                  # ADGVS_CAMERA_* settings
    python -m scan_agent camera-test --source picamera2 --rotate 90 --out ~/camtest
    python -m scan_agent camera-test --source opencv --device 0 --out camtest   # USB webcam
    python -m scan_agent camera-test --source file --device ./frames --out camtest

It waits for a page that is present and still (up to --timeout), focuses, takes the
full-resolution still (recapturing up to --attempts times for blur/exposure/glare), finds
and flattens the page and measures its quality, exactly as the station does. Writes:

    <out>/page.png      the normalized page (gray, A4 at 300 DPI) that would be uploaded
    <out>/frame.jpg     the full-resolution frame as captured (unless --no-frame)
    <out>/result.json   {"capture", "quality", "timings_ms"} (contract blocks) + diagnostics

Exit code: 0 quality ok, 1 captured with quality issues, 2 camera unavailable.
"""

import argparse
import dataclasses
import json
import logging
import time
from pathlib import Path

import cv2

from scan_agent.capture.camera import SOURCES, CameraError, CameraSource
from scan_agent.config import MAX_CAPTURE_ATTEMPTS, CameraSettings, QualityThresholds


def _write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _encode(ext: str, image, params=None) -> bytes:
    ok, buf = cv2.imencode(ext, image, params or [])
    if not ok:
        raise RuntimeError(f"Could not encode {ext}")
    return buf.tobytes()


def summary(page, thresholds: QualityThresholds) -> list[str]:
    q, t, d = page.quality, thresholds, page.detail
    waited = f"{d['wait']} ms, " + ("page present and still" if d["settled"]
                                    else "no still page before the timeout")
    return [
        f"Wait     {waited}",
        f"Focus    {'/'.join(d['focus_state'])}, {d['focus']} ms",
        f"Still    {d['still']} ms, {page.capture['width']}x{page.capture['height']}",
        f"Quality  {d['quality']} ms (find page, correct, measure)",
        f"Total    capture {page.timings_ms['capture']} ms + quality "
        f"{page.timings_ms['quality']} ms, {page.capture['attempts']} attempt(s)",
        "Verdict  " + ("OK" if q["ok"] else "ISSUES: " + ", ".join(q["issues"])),
        f"  blur_score     {q['blur_score']:>8}   (min {t.blur_min})",
        f"  brightness     {q['brightness']:>8}   ({t.brightness_min}-{t.brightness_max})",
        f"  glare_fraction {q['glare_fraction']:>8}   (max {t.glare_max})",
        f"  skew_deg       {q['skew_deg']:>8}   (max {t.skew_max_deg})",
        f"  coverage       {q['coverage']:>8}   (min {t.coverage_min})",
        f"  effective_dpi  {q['effective_dpi']:>8}   (min {t.dpi_min})",
    ]


def main(argv: list[str] | None = None) -> int:
    defaults = CameraSettings.from_env()
    parser = argparse.ArgumentParser(prog="python -m scan_agent camera-test",
                                     description="Capture one page and check its quality.")
    parser.add_argument("--source", choices=SOURCES, default=defaults.source,
                        help="camera backend (default: ADGVS_CAMERA_SOURCE or picamera2)")
    parser.add_argument("--device", default=defaults.device,
                        help="picamera2: camera number; opencv: index or URL; file: folder")
    parser.add_argument("--rotate", type=int, choices=(0, 90, 180, 270), default=defaults.rotate,
                        help="clockwise rotation that makes the page upright")
    parser.add_argument("--timeout", type=float, default=defaults.timeout,
                        help="seconds to wait for a still page")
    parser.add_argument("--no-wait", action="store_true",
                        help="shoot at once, without waiting for a still page")
    parser.add_argument("--attempts", type=int, default=MAX_CAPTURE_ATTEMPTS,
                        help="maximum captures when the frame is not usable")
    parser.add_argument("--out", type=Path, default=Path("camera-test"),
                        help="output folder (created)")
    parser.add_argument("--no-frame", action="store_true",
                        help="do not write the full-resolution frame")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")

    settings = dataclasses.replace(defaults, source=args.source, device=args.device,
                                   rotate=args.rotate, timeout=args.timeout)
    thresholds = QualityThresholds.from_env()
    source = CameraSource(args.source, settings, thresholds, max_attempts=args.attempts,
                          keep_frame=not args.no_frame)
    start = time.monotonic()
    try:
        source.open()
        opened = round((time.monotonic() - start) * 1000)
        print(f"Camera   {source.backend.describe()}, opened in {opened} ms")
        page = source.capture(wait=not args.no_wait)
    except CameraError as e:
        print(f"Camera unavailable: {e}")
        return 2
    finally:
        source.close()
    if page is None:
        print(f"Nothing to capture: no images in {args.device or '.'}")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    written = [args.out / "page.png"]
    _write(written[0], _encode(".png", page.image))
    if page.frame is not None:
        written.append(args.out / "frame.jpg")
        _write(written[-1], _encode(".jpg", page.frame, [cv2.IMWRITE_JPEG_QUALITY, 92]))
    result = {
        "capture": page.capture,
        "quality": page.quality,
        "timings_ms": page.timings_ms,
        "detail": {k: v for k, v in page.detail.items() if k != "preview"},
        "camera": {"device": source.backend.describe(), "open_ms": opened,
                   "settings": dataclasses.asdict(settings)},
        "thresholds": dataclasses.asdict(thresholds),
    }
    written.append(args.out / "result.json")
    with open(written[-1], "w", encoding="utf-8", newline="") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    for line in summary(page, thresholds):
        print(line)
    print("Wrote    " + ", ".join(str(p) for p in written))
    return 0 if page.quality["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
