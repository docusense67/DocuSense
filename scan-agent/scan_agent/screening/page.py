"""The page under screening: canonical size, one full-page OCR pass, phrase lookup.

Every screening step works on the same `Page`, so the page is read by Tesseract once
(the dominant cost: about 1 s per A4 page on the dev PC, 2-3 s on the Pi 5).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import cv2
import numpy as np

from scan_agent.config import MM_TO_PX
from scan_agent.processing import ocr as _ocr  # sets pytesseract's tesseract_cmd
from scan_agent.processing.cleanup import binarize, remove_rules, to_gray

from .profile import Zone

CANON_LONG_PX = 3508  # A4 long side at 300 DPI: mm zones and tolerances map to pixels
# Below this confidence (0-100) a word is not used as evidence of anything.
MIN_WORD_CONF = 30.0
# Full-page OCR scale, 1.0 = 300 DPI. ADGVS_SCREENING_OCR_SCALE < 1 reads a downscaled copy
# for slow stations; small 6-7 pt form labels suffer first, so check accuracy on the client
# samples (screening-eval) before lowering it.
OCR_SCALE = float(os.environ.get("ADGVS_SCREENING_OCR_SCALE", "1.0"))

_NON_ALNUM = re.compile(r"[^0-9A-Z]+")


def normalize(text: str) -> str:
    """Upper case, letters and digits only (spaces dropped): what phrase matching compares."""
    return _NON_ALNUM.sub("", text.upper())


@dataclass(frozen=True)
class Word:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int

    @property
    def norm(self) -> str:
        return normalize(self.text)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


Box = tuple[int, int, int, int]  # x0, y0, x1, y1 page pixels


def union(words: list[Word]) -> Box:
    return (min(w.x for w in words), min(w.y for w in words),
            max(w.x + w.w for w in words), max(w.y + w.h for w in words))


@dataclass(frozen=True)
class Match:
    words: tuple[Word, ...]
    ratio: float
    # Share of the phrase the matched words cover (start, end), 0..1: a heading read as
    # "CERTIFICATE OF COMPLE" covers (0, 0.84), so its centre is left of the full title's.
    span: tuple[float, float] = (0.0, 1.0)

    @property
    def box(self) -> Box:
        return union(list(self.words))

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.box
        return (x0 + x1) / 2, (y0 + y1) / 2

    @property
    def conf(self) -> float:
        return min(w.conf for w in self.words)


def min_ratio_for(phrase_norm: str) -> float:
    """Short phrases must match exactly ("AGE" is 86% similar to "PAGE")."""
    n = len(phrase_norm)
    if n <= 4:
        return 1.0
    if n <= 8:
        return 0.85
    # 0.88 still allows 2-3 misread characters in a title, but not a different title of
    # the same shape ("CERTIFICATE OF RECOGNITION" is 81% similar to "... OF COMPLETION").
    return 0.88


class Page:
    """A normalized page: grey, long side 3508 px (300 DPI for A4), with lazy OCR."""

    def __init__(self, image: np.ndarray):
        gray = to_gray(image)
        h, w = gray.shape
        scale = CANON_LONG_PX / max(h, w)
        if abs(scale - 1.0) > 0.002:
            gray = cv2.resize(gray, (round(w * scale), round(h * scale)),
                              interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
        self.gray = gray
        self.scale = scale if abs(scale - 1.0) > 0.002 else 1.0  # canonical px per input px
        self.height, self.width = gray.shape
        self._words: list[Word] | None = None
        self._segments: list[list[Word]] | None = None
        self.cache: dict = {}  # per-page results shared between screening steps

    # -- geometry ---------------------------------------------------------------------
    @staticmethod
    def mm(px: float) -> float:
        return px / MM_TO_PX

    @staticmethod
    def px(mm: float) -> float:
        return mm * MM_TO_PX

    def zone_box(self, zone: Zone, dx: float = 0.0, dy: float = 0.0, pad_mm: float = 0.0) -> Box:
        x, y, w, h = zone
        pad = pad_mm * MM_TO_PX
        x0 = max(0, round(x * self.width + dx - pad))
        y0 = max(0, round(y * self.height + dy - pad))
        x1 = min(self.width, round((x + w) * self.width + dx + pad))
        y1 = min(self.height, round((y + h) * self.height + dy + pad))
        return x0, y0, max(x0, x1), max(y0, y1)

    def zone_center(self, zone: Zone) -> tuple[float, float]:
        x, y, w, h = zone
        return (x + w / 2) * self.width, (y + h / 2) * self.height

    def input_box(self, box: Box) -> list[int]:
        """[x, y, w, h] in the pixels of the image that was passed in (the uploaded page)."""
        x0, y0, x1, y1 = box
        return [round(x0 / self.scale), round(y0 / self.scale), round((x1 - x0) / self.scale),
                round((y1 - y0) / self.scale)]

    @property
    def landscape(self) -> bool:
        return self.width > self.height

    # -- OCR --------------------------------------------------------------------------
    @property
    def words(self) -> list[Word]:
        if self._words is None:
            self._words = read_words(self.gray, OCR_SCALE)
        return self._words

    @property
    def segments(self) -> list[list[Word]]:
        """Words grouped into line segments (same row, no wide gap), left to right."""
        if self._segments is None:
            self._segments = group_segments(self.words)
        return self._segments

    def words_in(self, box: Box, min_conf: float = MIN_WORD_CONF) -> list[Word]:
        x0, y0, x1, y1 = box
        return [w for w in self.words
                if w.conf >= min_conf and x0 <= w.cx <= x1 and y0 <= w.cy <= y1]

    def find(self, phrase: str, near: tuple[float, float] | None = None,
             radius: float | None = None, min_ratio: float | None = None) -> Match | None:
        """The phrase as a run of consecutive words in one line segment. With `near`, the
        closest acceptable match within `radius` px; otherwise the best-scoring one."""
        target = normalize(phrase)
        if not target:
            return None
        threshold = min_ratio_for(target) if min_ratio is None else min_ratio
        n_words = max(1, len(phrase.split()))
        matcher = SequenceMatcher(None, autojunk=False)
        matcher.set_seq2(target)
        found: list[Match] = []
        for segment in self.segments:
            if near is not None and radius is not None and not _segment_near(segment, near,
                                                                              radius):
                continue
            usable = [w for w in segment if w.conf >= MIN_WORD_CONF or len(w.norm) > 2]
            for size in range(max(1, n_words - 1), n_words + 3):
                for start in range(0, len(usable) - size + 1):
                    window = usable[start:start + size]
                    candidate = "".join(w.norm for w in window)
                    if not candidate or abs(len(candidate) - len(target)) > max(
                            2, 0.3 * len(target)):
                        continue
                    matcher.set_seq1(candidate)
                    if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold:
                        continue
                    ratio = matcher.ratio()
                    if ratio >= threshold:
                        blocks = [b for b in matcher.get_matching_blocks() if b.size]
                        span = (blocks[0].b / len(target),
                                (blocks[-1].b + blocks[-1].size) / len(target))
                        found.append(Match(tuple(window), ratio, span))
        if not found:
            return None
        if near is not None:
            def distance(m: Match) -> float:
                cx, cy = m.center
                return float(np.hypot(cx - near[0], cy - near[1]))
            found = [m for m in found if radius is None or distance(m) <= radius]
            if not found:
                return None
            # Repeated labels ("(First)" for child, mother, father) are far apart: take the
            # best-scoring match among those close to the nearest one.
            nearest = min(distance(m) for m in found)
            close = [m for m in found if distance(m) <= nearest + 10 * MM_TO_PX]
            return max(close, key=lambda m: (m.ratio, len(m.words), -distance(m)))
        return max(found, key=lambda m: (m.ratio, -len(m.words)))


def _segment_near(segment: list[Word], near: tuple[float, float], radius: float) -> bool:
    x0, y0, x1, y1 = union(segment)
    dx = max(x0 - near[0], 0, near[0] - x1)
    dy = max(y0 - near[1], 0, near[1] - y1)
    return dx * dx + dy * dy <= radius * radius


def ocr_input(gray: np.ndarray) -> np.ndarray:
    """Rules erased (Tesseract drops boxed text otherwise, see CLAUDE.md), thresholded."""
    return binarize(remove_rules(gray, min_len_px=100))


def read_words(gray: np.ndarray, scale: float = 1.0) -> list[Word]:
    image = ocr_input(gray)
    if scale != 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    words = []
    for line in _ocr._lines(image, "--oem 1 --psm 11"):
        for w in line.words:
            if not w.text.strip() or w.width <= 0 or w.height <= 0:
                continue
            words.append(Word(w.text, w.confidence, round(w.left / scale), round(w.top / scale),
                              round(w.width / scale), round(w.height / scale)))
    return words


def group_segments(words: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.cy):
        for row in rows:
            ref = row[-1]
            if abs(word.cy - ref.cy) < 0.5 * max(min(word.h, ref.h), 8):
                row.append(word)
                break
        else:
            rows.append([word])
    segments = []
    for row in rows:
        row.sort(key=lambda w: w.x)
        height = float(np.median([w.h for w in row]))
        current = [row[0]]
        for word in row[1:]:
            prev = current[-1]
            if word.x - (prev.x + prev.w) > 2.2 * height:
                segments.append(current)
                current = [word]
            else:
                current.append(word)
        segments.append(current)
    return segments
