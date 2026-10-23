"""Zone OCR with Tesseract 5 (Section 5J). Printed text only; no handwriting."""

import re
from dataclasses import dataclass, field
from time import monotonic

import cv2
import numpy as np
import pytesseract

from scan_agent.config import tesseract_cmd
from scan_agent.processing.cleanup import binarize, crop_mm, remove_rules, to_gray

pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()

UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Restricting characters for structured fields removes whole classes of misreads (O vs 0).
FIELD_WHITELIST = {
    "student_number": "0123456789-",
    "document_number": UPPER + "0123456789-",
    "gwa": "0123456789.",
    "academic_year": "0123456789-",
}


def _straighten_display(gray: np.ndarray) -> np.ndarray:
    """Estimate camera tilt from a consistent band of long, near-horizontal edges."""
    height, width = gray.shape
    edges = cv2.Canny(gray, 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 60,
        minLineLength=max(60, width * .12), maxLineGap=30)
    if lines is None:
        return gray
    values = []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
        angle = np.degrees(np.arctan2(float(y2 - y1), float(x2 - x1)))
        if abs(angle) <= 35:
            values.append((angle, np.hypot(float(x2 - x1), float(y2 - y1))))
    if len(values) < 2:
        return gray
    angles, weights = np.array(values).T
    histogram, bins = np.histogram(angles, bins=np.arange(-36, 37, 2), weights=weights)
    center = bins[int(histogram.argmax())] + 1
    band = abs(angles - center) <= 3
    angle = float(np.average(angles[band], weights=weights[band]))
    if abs(angle) < 1:
        return gray
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sine + width * cosine)
    new_height = int(height * cosine + width * sine)
    matrix[0, 2] += (new_width - width) / 2
    matrix[1, 2] += (new_height - height) / 2
    return cv2.warpAffine(gray, matrix, (new_width, new_height), borderValue=255)


def _sparse_candidate(image: np.ndarray, timeout: float) -> tuple[str, float]:
    data = pytesseract.image_to_data(image, config='--oem 1 --psm 11',
        output_type=pytesseract.Output.DICT, timeout=timeout)
    lines: dict[tuple, list[str]] = {}
    confidences, counts = [], []
    for index, word in enumerate(data['text']):
        confidence = float(data['conf'][index])
        count = sum(character.isalnum() for character in word)
        if confidence < 60 or count < 2:
            continue
        key = (data['block_num'][index], data['par_num'][index], data['line_num'][index])
        lines.setdefault(key, []).append(word.strip())
        confidences.append(confidence)
        counts.append(count)
    if not confidences or sum(confidences) / len(confidences) < 75:
        return '', 0
    # Short labels such as LTE or WIFI are still legitimate readable text.
    text = '\n'.join(' '.join(words) for words in lines.values())
    return text, sum(count * confidence
                     for count, confidence in zip(counts, confidences, strict=True))


def ocr_page_text(page: np.ndarray) -> str:
    """Transcription only; independent of zone OCR and authenticity decisions."""
    gray = to_gray(page)
    text = pytesseract.image_to_string(
        gray, config='--oem 1 --psm 3', timeout=20).strip()
    if sum(character.isalnum() for character in text) >= 24:
        return text
    # Camera labels may be large, tilted, or white on dark backgrounds. Resize
    # without stretching, straighten, and test both polarities of local contrast.
    if gray.shape[1] > 1200:
        gray = cv2.resize(gray, (1200, round(gray.shape[0] * 1200 / gray.shape[1])),
                          interpolation=cv2.INTER_AREA)
    straight = _straighten_display(gray)
    clahe = cv2.createCLAHE(clipLimit=2, tileGridSize=(8, 8))
    contrast = clahe.apply(straight)
    candidates = [contrast, 255 - contrast]
    if straight is not gray:
        original = clahe.apply(gray)
        candidates.extend([original, 255 - original])
    best, score = text, 0.0
    deadline = monotonic() + 12
    for image in candidates:
        remaining = deadline - monotonic()
        if remaining < .5:
            break
        try:
            candidate, candidate_score = _sparse_candidate(image, min(4, remaining))
        except RuntimeError:
            continue
        if candidate_score > score:
            best, score = candidate, candidate_score
    return best


@dataclass
class Word:
    text: str
    confidence: float
    # Pixel box within the OCR'd region (0 when unknown, e.g. in parser unit tests).
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0


@dataclass
class OcrLine:
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def confidence(self) -> float:
        # Conservative: a line is only as trustworthy as its weakest word.
        return min((w.confidence for w in self.words), default=0.0)

    @property
    def box(self) -> tuple[int, int, int, int] | None:
        """(left, top, right, bottom) in region pixels, or None if positions are unknown."""
        sized = [w for w in self.words if w.width and w.height]
        if not sized:
            return None
        return (min(w.left for w in sized), min(w.top for w in sized),
                max(w.left + w.width for w in sized), max(w.top + w.height for w in sized))


def _lines(image: np.ndarray, config: str) -> list[OcrLine]:
    data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)
    grouped: dict[tuple, OcrLine] = {}
    for i, text in enumerate(data["text"]):
        if not text.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, OcrLine()).words.append(Word(
            text.strip(), float(data["conf"][i]), data["left"][i], data["top"][i],
            data["width"][i], data["height"][i]))
    return [grouped[k] for k in sorted(grouped)]


_ORDINAL = re.compile(r"^[lI|!](?=(st|ST)\b)")
_FOURTH = re.compile(r"^A(?=(th|TH)\b)")  # "4th" read as "Ath"
# A roman-numeral name suffix (II, III) read with l or | for I. Names are printed in upper
# case (backend documents/context.format_name), so a word of only I, l and | is one.
_SUFFIX = re.compile(r"[Il|]{2,3}")
_HEX_FIX = str.maketrans({"l": "1", "I": "1", "i": "1", "|": "1", "O": "0", "o": "0"})


def correct(name: str, text: str) -> str:
    """Fix confusions that a field's known format makes unambiguous (l vs 1, O vs 0)."""
    if name == "year_level":
        return _FOURTH.sub("4", _ORDINAL.sub("1", text))
    if name == "semester":
        return _ORDINAL.sub("1", text)
    if name == "student_name":  # applied to the name's last word
        return "I" * len(text) if _SUFFIX.fullmatch(text) else text
    if name == "ref":
        return text.translate(_HEX_FIX).lower()
    return text


def ocr_field(page: np.ndarray, zone: dict, name: str = "") -> OcrLine:
    """Single-line field. Returns the words with Tesseract's per-word confidence (0-100).
    Thresholded first, like tables (see `cleanup.binarize`): on simulated rough scans the
    GWA went from 75% to 100% read correctly. Not enlarged: at 1.5x "4th" was read "Ath"
    in 10 of 60 clean COEs."""
    region = binarize(crop_mm(page, zone, pad_mm=0.3))
    config = "--oem 1 --psm 7"
    if name in FIELD_WHITELIST:
        config += f" -c tessedit_char_whitelist={FIELD_WHITELIST[name]}"
    merged = OcrLine()
    for line in _lines(region, config):
        merged.words.extend(line.words)
    if merged.words and name in ("year_level", "semester"):
        first = merged.words[0]
        merged.words[0] = Word(correct(name, first.text), first.confidence)
    if len(merged.words) > 1 and name == "student_name":
        last = merged.words[-1]
        merged.words[-1] = Word(correct(name, last.text), last.confidence,
                                last.left, last.top, last.width, last.height)
    return merged


_CODE = re.compile(r"[A-Z]{2,6}(?:\d{1,4}[A-Z]?)?")  # parsing.CODE without the inner space
_GRADE = re.compile(r"[1-5]\.\d{2}\.?|INC|DRP")  # parsing.GRADE; fix_grade drops a final "."
# A table row starts with its subject code, alone in a narrow cell before a wide gap. There
# the LSTM sometimes misreads a character with low confidence ("ELEC1" -> "ELEC]" at 18,
# "IT102" -> "{T102" at 54). Below this confidence the code alone is read again. Grades are
# read again whenever they are not well-formed ("2./5"; "1 00" for an overprinted "1.00",
# which reads "1.00" at 95 alone); a well-formed grade is left as read, with its confidence
# for the backend to judge.
REREAD_CODE_BELOW = 60.0
# A code cell is small, and short codes ("IT102") read better enlarged. Grade cells are
# read at scan size: enlarged, the LSTM read a clean printed "2.75" as "2.15" at confidence
# 81-95 (CI set, TOR-2026-000019), the 7 -> 1 confusion behind most wrong grade reads.
CODE_REREAD_SCALE = 2.0
GRADE_REREAD_SCALE = 1.0


def _read_alone(region: np.ndarray, box: tuple[int, int, int, int],
                scale: float) -> Word | None:
    """One word read from a (left, top, right, bottom) box of the grey region, thresholded
    on its own, with Tesseract's single-line mode; None unless exactly one word."""
    left, top, right, bottom = (max(0, v) for v in box)
    crop = region[top:bottom, left:right]
    if crop.size == 0:
        return None
    again = [w for ln in _lines(binarize(crop, scale), "--oem 1 --psm 7") for w in ln.words]
    return again[0] if len(again) == 1 else None


def _reread_row_cells(region: np.ndarray, lines: list[OcrLine]) -> None:
    """Read a table row's low-confidence code (first word) or malformed grade (before the
    remarks) again, alone, from the space between its neighbours. This is a second reading
    of the same cell, never a guess at what was printed; its own confidence is reported.
    Word boxes are region pixels.
    (A character whitelist does worse here: the LSTM then returns "ELECL" at confidence 0
    for "ELEC1".)"""
    for line in lines:
        words = line.words
        if len(words) < 5 or not words[-1].text.isalpha() or not all(w.width for w in words):
            continue
        first, second = words[0], words[1]
        if first.confidence < REREAD_CODE_BELOW:
            pad = max(3, first.height // 2)
            new = _read_alone(region, (first.left - pad, first.top - pad,
                                       max(second.left - pad // 2, first.left + first.width),
                                       first.top + first.height + pad), CODE_REREAD_SCALE)
            # A code that was not well-formed matches no issued row anyway, so a well-formed
            # re-read replaces it even at lower confidence.
            if new and _CODE.fullmatch(new.text) and (
                    not _CODE.fullmatch(first.text) or new.confidence > first.confidence):
                words[0] = Word(new.text, new.confidence, first.left, first.top,
                                first.width, first.height)
        # The grade is the word before the remarks, or two words when its point was read
        # as a gap ("3 1 00 Passed", as in parsing._join_split_grade); the units come first.
        split = (len(words) >= 6 and re.fullmatch(r"[1-5]", words[-3].text)
                 and re.fullmatch(r"[0-9Oo]{2}", words[-2].text)
                 and re.fullmatch(r"\d+(?:\.\d+)?", words[-4].text))
        span = 2 if split else 1
        grade, units, remarks = words[-1 - span:-1], words[-2 - span], words[-1]
        if span == 1 and _GRADE.fullmatch(grade[0].text):
            continue
        top = min(w.top for w in grade)
        bottom = max(w.top + w.height for w in grade)
        pad = max(3, (bottom - top) // 5)
        new = _read_alone(region, (units.left + units.width + pad, top - pad,
                                   remarks.left - pad, bottom + pad), GRADE_REREAD_SCALE)
        # A grade re-read must also be more confident than the first reading: a different
        # value could decide the document.
        if (new and _GRADE.fullmatch(new.text)
                and new.confidence > min(w.confidence for w in grade)):
            left = grade[0].left
            right = max(w.left + w.width for w in grade)
            words[-1 - span:-1] = [Word(new.text, new.confidence, left, top, right - left,
                                        bottom - top)]


def ocr_block(page: np.ndarray, zone: dict, strip_rules: bool = False,
              scale: float = 1.0) -> list[OcrLine]:
    """Multi-line region such as the grades table; rules are erased first for tables, and
    a table row's doubtful code or grade is read again on its own. The region is
    thresholded before OCR (see `cleanup.binarize`). `scale` enlarges it first; word boxes
    are mapped back to the region's own pixels."""
    region = crop_mm(page, zone)
    if strip_rules:
        region = remove_rules(region)
    lines = _lines(binarize(region, scale), "--oem 1 --psm 6")
    if scale != 1.0:
        for line in lines:
            line.words = [Word(w.text, w.confidence, round(w.left / scale),
                               round(w.top / scale), round(w.width / scale),
                               round(w.height / scale)) for w in line.words]
    if strip_rules:
        _reread_row_cells(region, lines)
    return lines
