"""Applicant intake reading (docs/applicant-intake.md): OCR text -> document type and fields.

Pure functions, no I/O. The worker only extracts what is printed; the backend decides
(services/applicant_intake.decide). A confident classification or a match proves nothing about
a document's authenticity, and nothing here contacts a cloud service.

The classifier is a small multinomial Naive Bayes model over a synthetic keyword corpus. It
tells a Transcript of Records from a Certificate of Enrollment and from anything else; its
confidence is what the backend's low-confidence rule reads.
"""

import math
import re
from collections import Counter, defaultdict

READING_SCHEMA = 1
TOR = "transcript_of_records"
COE = "certificate_of_enrollment"
UNKNOWN = "unknown"
MIN_READABLE_CHARS = 40  # letters/digits per page below this: the page could not be read
MAX_TEXT_CHARS = 20_000  # per page, sent for staff review
MAX_FIELD_CHARS = 200

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
MIN_KNOWN_WORDS = 4


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


def _field(text: str, name: str) -> str | None:
    value = VALUE_PATTERNS.get(name, r"(.+)")
    match = re.search(r"^\s*" + FIELD_PATTERNS[name] + r"\s*[:\-]?\s*" + value + r"\s*$",
                      text, re.I | re.M)
    if not match:
        return None
    found = re.sub(r"\s+", " ", match.group(1)).strip()
    return found[:MAX_FIELD_CHARS] or None


def school_name(text: str) -> str | None:
    """The first line that names a school (usually the letterhead)."""
    for line in text.splitlines()[:8]:
        clean = line.strip()
        if re.search(r"\b(university|college|institute|school|academy)\b", clean, re.I):
            return clean[:MAX_FIELD_CHARS]
    return None


def extract_fields(text: str) -> dict[str, dict]:
    """Printed values only; confidence is not measured per field (None)."""
    fields = {name: _field(text, name) for name in FIELD_PATTERNS}
    fields["school_name"] = school_name(text)
    return {name: {"value": value, "confidence": None}
            for name, value in fields.items() if value}


def readable(text: str) -> bool:
    return sum(c.isalnum() for c in text) >= MIN_READABLE_CHARS


def build_reading(page_texts: list[str]) -> dict:
    """The reading the backend expects (schema 1), from the OCR text of every page."""
    text = "\n\n".join(page_texts)
    label, confidence = MODEL.predict(text)
    return {
        "schema": READING_SCHEMA,
        "quality": {"ok": bool(page_texts) and all(readable(t) for t in page_texts),
                    "unreadable_pages": [i for i, t in enumerate(page_texts, 1)
                                         if not readable(t)]},
        "doc_type": {"predicted": label, "confidence": confidence,
                     "model": "naive-bayes-synthetic-v1"},
        "fields": extract_fields(text),
        "pages": [{"page": i, "ocr_text": t[:MAX_TEXT_CHARS]}
                  for i, t in enumerate(page_texts, 1)],
        "engine": "tesseract local",
    }
