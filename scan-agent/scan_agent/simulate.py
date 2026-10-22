"""Turn issued PDFs into simulated 300 DPI scans, for an accuracy pre-check before real paper.

    python -m scan_agent.simulate --pdfs ocr-set/pdfs --out ocr-set/scans-clean --level clean

Levels:
  clean  what a decent ADF produces: up to 1 degree skew, a few mm offset with the dark
         scanner bed showing, light noise and blur, slight brightness change, JPEG artefacts.
         Section 21's 98% target applies to clean scans.
  rough  worse than a scanner should be: more skew, noise and blur, washed-out contrast.
         For information only; it shows which fields degrade first.

Pages are written in stack order into batch-NN/ folders of `--batch` documents each, the way
bulk stacks arrive. Deterministic for a given --seed.
"""

import argparse
import random
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pypdfium2

DPI = 300
MM = DPI / 25.4


@dataclass(frozen=True)
class Level:
    max_angle: float
    max_offset_mm: float
    noise: float
    blur: float
    contrast: tuple[float, float]
    jpeg: int


LEVELS = {
    "clean": Level(max_angle=1.0, max_offset_mm=3, noise=4, blur=0.5, contrast=(0.92, 1.0),
                   jpeg=85),
    "rough": Level(max_angle=2.5, max_offset_mm=6, noise=10, blur=1.1, contrast=(0.70, 0.85),
                   jpeg=60),
}


def degrade(page: np.ndarray, level: Level, rng: random.Random) -> np.ndarray:
    h, w = page.shape
    pad = int(10 * MM)
    canvas = np.full((h + 2 * pad, w + 2 * pad), 35, np.uint8)  # dark scanner bed
    dx = int(rng.uniform(-level.max_offset_mm, level.max_offset_mm) * MM)
    dy = int(rng.uniform(-level.max_offset_mm, level.max_offset_mm) * MM)
    canvas[pad + dy:pad + dy + h, pad + dx:pad + dx + w] = page
    angle = rng.uniform(-level.max_angle, level.max_angle)
    matrix = cv2.getRotationMatrix2D((canvas.shape[1] / 2, canvas.shape[0] / 2), angle, 1.0)
    img = cv2.warpAffine(canvas, matrix, canvas.shape[::-1], flags=cv2.INTER_LINEAR,
                         borderValue=35).astype(np.float32)
    low, high = level.contrast
    img = 128 + (img - 128) * rng.uniform(low, high) + rng.uniform(-8, 8)
    img += np.random.default_rng(rng.randint(0, 2**31)).normal(0, level.noise, img.shape)
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (0, 0), level.blur)
    ok, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, level.jpeg])
    return cv2.imdecode(jpeg, cv2.IMREAD_GRAYSCALE)


def simulate(pdfs: Path, out: Path, level: Level, batch: int, seed: int,
             limit: int | None = None) -> int:
    rng = random.Random(seed)
    files = sorted(pdfs.glob("*.pdf"))[:limit]
    pages = 0
    for index, pdf in enumerate(files):
        folder = out / f"batch-{index // batch + 1:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        for page_no, page in enumerate(pypdfium2.PdfDocument(str(pdf)), start=1):
            image = np.array(page.render(scale=DPI / 72).to_pil().convert("L"))
            cv2.imwrite(str(folder / f"{index:04d}-{page_no:02d}.png"), degrade(image, level, rng))
            pages += 1
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scan_agent.simulate")
    parser.add_argument("--pdfs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--level", choices=LEVELS, default="clean")
    parser.add_argument("--batch", type=int, default=10, help="documents per stack")
    parser.add_argument("--limit", type=int, help="only the first N documents")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    pages = simulate(args.pdfs, args.out, LEVELS[args.level], args.batch, args.seed, args.limit)
    print(f"{pages} simulated {args.level} page(s) in {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
