"""Applicant intake reading (docs/applicant-intake.md): OCR text -> document type and fields,
and the layout measurements the backend compares with a school's reference sample.

Pure functions, no I/O. The worker only extracts and measures what is printed; the backend
decides (services/applicant_intake, services/applicant_references). A confident classification
or a match with a reference sample proves nothing about a document's authenticity, and nothing
here contacts a cloud service.

The classifier is a small multinomial Naive Bayes model over a synthetic keyword corpus,
extended with the school's approved training reference samples (sent by the API with the job).
Held-out reference samples never train it: they give its evaluation, which goes into the reading
with the model version.
"""

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from statistics import median

READING_SCHEMA = 1
TOR = "transcript_of_records"
COE = "certificate_of_enrollment"
UNKNOWN = "unknown"
MIN_READABLE_CHARS = 40  # letters/digits per page below this: the page could not be read
MAX_TEXT_CHARS = 20_000  # per page, sent for staff review
MAX_FIELD_CHARS = 200
SYNTHETIC_MODEL = "naive-bayes-synthetic-v1"

# Synthetic: wording a TOR / COE usually has, and documents that are neither.
CORPUS = [
    (TOR, "official transcript of records student name program course code descriptive "
          "title units grade remarks semester cumulative gwa registrar"),
    (TOR, "transcript academic record grades subjects credits units semester academic year "
          "grade point average university registrar"),
    (TOR, "student transcript of records scholastic record degree subjects final grades "
          "units date issued registrar"),
    (COE, "certificate of enrollment this is to certify student name is officially enrolled "
          "program year level semester academic year purpose registrar"),
    (COE, "certification of enrolment currently enrolled student academic year semester "
          "course program university registrar"),
    (COE, "certificate enrollment enrolled school year student number year level issued upon "
          "request purpose registrar"),
    (UNKNOWN, "birth certificate date of birth place of birth mother father civil registry "
              "statistics authority"),
    (UNKNOWN, "receipt invoice total payment cash amount balance bill address order quantity "
              "price"),
    (UNKNOWN, "good moral character recommendation employment contract agreement signature "
              "identification passport license"),
]
# Synthetic evaluation examples (never training data).
SYNTHETIC_TEST = [
    (TOR, "transcript of records semester grades units grade point average"),
    (COE, "certificate of enrollment officially enrolled academic year semester"),
    (UNKNOWN, "invoice receipt cash total payment amount"),
]
MIN_KNOWN_WORDS = 4
CLASSES = {TOR, COE, UNKNOWN}


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]{2,}", text.casefold())


class Model:
    def __init__(self, samples: list[tuple[str, str]]):
        self.counts: dict[str, Counter] = defaultdict(Counter)
        self.totals: Counter = Counter()
        self.documents: Counter = Counter()
        for label, text in samples:
            words = tokens(text)
            self.counts[label].update(words)
            self.totals[label] += len(words)
            self.documents[label] += 1
        self.vocab = set().union(*(set(c) for c in self.counts.values()))

    def predict(self, text: str) -> tuple[str, float]:
        """(label, confidence 0-1). Too few known words: unknown with confidence 0."""
        words = Counter(tokens(text))
        if sum(n for w, n in words.items() if w in self.vocab) < MIN_KNOWN_WORDS:
            return UNKNOWN, 0.0
        documents = sum(self.documents.values())
        scores = {}
        for label, count in self.counts.items():
            score = math.log(self.documents[label] / documents)
            for word, times in words.items():
                if word in self.vocab:
                    score += times * math.log((count[word] + 1)
                                              / (self.totals[label] + len(self.vocab)))
            scores[label] = score
        best = max(scores, key=scores.get)
        confidence = 1 / sum(math.exp(value - scores[best]) for value in scores.values())
        return best, round(confidence, 4)


MODEL = Model(CORPUS)


def _accuracy(model: Model, samples: list[tuple[str, str]]) -> float | None:
    if not samples:
        return None
    return round(sum(model.predict(text)[0] == label for label, text in samples)
                 / len(samples), 4)


def classify(text: str, references: Iterable[dict] = ()) -> dict:
    """The `doc_type` block of a reading. `references`: the API's classifier corpus, approved
    reference samples {"id", "class", "use", "text"}; training ones extend the synthetic corpus,
    held-out ones only evaluate the model."""
    training, held_out, training_ids = [], [], []
    for ref in references:
        cls, sample = ref.get("class"), str(ref.get("text") or "")
        if cls not in CLASSES or not sample.strip():
            continue
        if ref.get("use") == "held_out":
            held_out.append((cls, sample))
        else:
            training.append((cls, sample))
            training_ids.append(ref.get("id"))
    if training:
        model = Model(CORPUS + training)
        digest = hashlib.sha256(json.dumps(CORPUS + training).encode()).hexdigest()[:16]
        name = f"naive-bayes-{digest}"
    else:
        model, name = MODEL, SYNTHETIC_MODEL
    label, confidence = model.predict(text)
    return {"predicted": label, "confidence": confidence, "model": name,
            "synthetic_only": not training, "training_reference_ids": training_ids,
            "evaluation": {"synthetic_test_count": len(SYNTHETIC_TEST),
                           "synthetic_test_accuracy": _accuracy(model, SYNTHETIC_TEST),
                           "held_out_count": len(held_out),
                           "held_out_accuracy": _accuracy(model, held_out)}}


# Printed labels -> backend field names (services/intake_fields.py TOR/COE "sources").
FIELD_PATTERNS = {
    "student_name": r"(?:student'?s?\s*name|name\s+of\s+student|name)",
    "student_number": r"(?:student\s*(?:no\.?|number|id)|id\s*(?:no\.?|number))",
    "program": r"(?:program|degree|course)",
    "school_year": r"(?:academic|school)\s*year",
    "date_issued": r"(?:date\s*(?:issued|of\s*issue)|issued\s*on)",
}
VALUE_PATTERNS = {
    "student_number": r"([A-Z0-9][A-Z0-9\-]{2,39})",
    "school_year": r"(\d{4}\s*[-–]\s*\d{4})",
}


def _clean(value: str) -> str | None:
    found = re.sub(r"\s+", " ", value).strip()
    return found[:MAX_FIELD_CHARS] or None


def _field(text: str, name: str) -> str | None:
    value = VALUE_PATTERNS.get(name, r"(.+)")
    match = re.search(r"^\s*" + FIELD_PATTERNS[name] + r"\s*[:\-]?\s*" + value + r"\s*$",
                      text, re.I | re.M)
    return _clean(match.group(1)) if match else None


def _aliased(text: str, aliases: list[str]) -> str | None:
    """The value printed after the first of a school's own labels found on a line."""
    for alias in aliases:
        match = re.search(r"^\s*" + re.escape(alias.strip()) + r"\s*[:\-]?\s*(.+?)\s*$",
                          text, re.I | re.M)
        if match and _clean(match.group(1)):
            return _clean(match.group(1))
    return None


def school_name(text: str) -> str | None:
    """The first line that names a school (usually the letterhead)."""
    for line in text.splitlines()[:8]:
        clean = line.strip()
        if re.search(r"\b(university|college|institute|school|academy)\b", clean, re.I):
            return clean[:MAX_FIELD_CHARS]
    return None


def extract_fields(text: str, field_labels: dict[str, list[str]] | None = None
                   ) -> dict[str, dict]:
    """Printed values only; confidence is not measured per field (None). `field_labels`: the
    school's printed label aliases from its reference sample (field name -> labels); they are
    tried first and may name fields of their own."""
    fields = {name: _field(text, name) for name in FIELD_PATTERNS}
    fields["school_name"] = school_name(text)
    for name, aliases in (field_labels or {}).items():
        found = _aliased(text, list(aliases or []))
        if found:
            fields[str(name)] = found
    return {name: {"value": value, "confidence": None}
            for name, value in fields.items() if value}


def readable(text: str) -> bool:
    return sum(c.isalnum() for c in text) >= MIN_READABLE_CHARS


# ---- layout measurements (from Tesseract's word boxes) ----------------------------------------

# Words whose position is compared with the reference sample (text anchors).
ANCHOR_WORDS = {"transcript", "certificate", "enrollment", "enrolment", "records", "name",
                "program", "course", "grade", "grades", "units", "semester", "registrar",
                "remarks", "student", "date", "school", "year"}


def layout_from_data(data: dict, width: int, height: int) -> dict:
    """One page's layout from pytesseract.image_to_data (dict output): the first position of
    each anchor word, median word height and gap, median line gap and the line count, all as
    fractions of the page size."""
    lines: dict[tuple, list[tuple[int, int, int, int]]] = defaultdict(list)
    anchors: dict[str, list[float]] = {}
    heights = []
    for i, raw in enumerate(data.get("text", [])):
        word = str(raw or "").strip()
        if not word:
            continue
        x, y, w, h = (int(data[k][i]) for k in ("left", "top", "width", "height"))
        lines[(data["block_num"][i], data["par_num"][i], data["line_num"][i])].append(
            (x, x + w, y, h))
        heights.append(h / height)
        normalized = re.sub(r"[^a-z]", "", word.casefold())
        if normalized in ANCHOR_WORDS and normalized not in anchors:
            anchors[normalized] = [round(min(max(x / width, 0), 1), 4),
                                   round(min(max(y / height, 0), 1), 4)]
    gaps = []
    for words in lines.values():
        ordered = sorted(words)
        gaps.extend(max(0, b[0] - a[1]) / width for a, b in zip(ordered, ordered[1:],
                                                                    strict=False))
    tops = sorted({min(w[2] for w in words) / height for words in lines.values()})
    line_gaps = [b - a for a, b in zip(tops, tops[1:], strict=False)]
    return {"anchors": anchors,
            "median_word_height": _unit(median(heights)) if heights else 0.0,
            "median_word_gap": _unit(median(gaps)) if gaps else 0.0,
            "median_line_gap": _unit(median(line_gaps)) if line_gaps else 0.0,
            "line_count": len(lines)}


def _unit(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 5)


def text_from_data(data: dict) -> str:
    """The page text from image_to_data, one line per Tesseract line."""
    lines: dict[tuple, list[str]] = defaultdict(list)
    for i, raw in enumerate(data.get("text", [])):
        word = str(raw or "").strip()
        if word:
            lines[(data["block_num"][i], data["par_num"][i], data["line_num"][i])].append(word)
    return "\n".join(" ".join(words) for words in lines.values())


def build_reading(page_texts: list[str], layouts: list[dict] | None = None,
                  references: Iterable[dict] = (),
                  field_labels: dict[str, list[str]] | None = None,
                  engine: str = "tesseract local") -> dict:
    """The reading the backend expects (schema 1), from the OCR text of every page and, when
    measured, each page's layout."""
    text = "\n\n".join(page_texts)
    reading = {
        "schema": READING_SCHEMA,
        "quality": {"ok": bool(page_texts) and all(readable(t) for t in page_texts),
                    "unreadable_pages": [i for i, t in enumerate(page_texts, 1)
                                         if not readable(t)]},
        "doc_type": classify(text, references),
        "fields": extract_fields(text, field_labels),
        "pages": [{"page": i, "ocr_text": t[:MAX_TEXT_CHARS]}
                  for i, t in enumerate(page_texts, 1)],
        "engine": engine,
    }
    if layouts:
        reading["layout"] = {"pages": [{"page": i, **layout}
                                       for i, layout in enumerate(layouts, 1)]}
    return reading
