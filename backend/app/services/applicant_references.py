"""Reference samples and the assessment of an online upload against them: pure, no I/O.

docs/applicant-intake.md, "References". Staff upload labelled samples of each issuing school's
TOR/COE; the OCR worker reads and measures them like an upload, and staff approve the reading.
When an applicant's upload is read, the worker sends layout measurements (text anchors,
median word height and gap, line gap, page count). This module alone compares them with the
latest approved genuine-sample training reference of the same school and type, and turns every
difference into a reason code (services/intake.REASON_TEXT).

A match with a reference sample is not proof of authenticity: it only means no rule fired.

The nearest-neighbour layout model is used only when the school has approved training samples
labelled both "genuine sample" and "altered sample" for that type. Its verdict is preliminary:
it can send a document to manual review, never pass one on its own.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import IntakeDocType, ReferenceLabel, ReferenceUse
from app.services import intake as screening

DEFAULT_REQUIRED_FIELDS = ("student_name",)
DEFAULT_LAYOUT_TOLERANCE = 0.15  # normalised page distance an anchor may move
MIN_LAYOUT_TOLERANCE, MAX_LAYOUT_TOLERANCE = 0.02, 0.5
SPACING_TOLERANCE = 0.5  # relative difference of a text metric before it counts
LAYOUT_MODEL_MIN_CONFIDENCE = 0.75
MAX_RULE_FIELDS = 20
MAX_ALIASES = 10
MAX_ALIAS_CHARS = 99
MAX_LAYOUT_PAGES = 50
MAX_ANCHORS = 40
MAX_CORPUS_CHARS = 20_000  # per reference, sent to the worker's classifier
FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ANCHOR_NAME = re.compile(r"^[a-z]{1,30}$")
RULE_KEYS = {"required_fields", "field_labels", "layout_tolerance"}
METRICS = ("word_height", "word_gap", "line_gap")
# Abbreviations that often differ between how an applicant and the letterhead write a name.
ABBREVIATIONS = {"univ": "university", "coll": "college", "inst": "institute",
                 "acad": "academy", "sch": "school"}
# Assessment reason codes, in the order they are listed.
ASSESSMENT_ORDER = (
    screening.REFERENCE_FIELD_MISSING, screening.LAYOUT_NOT_MEASURED,
    screening.PAGE_COUNT_DIFFERS, screening.LAYOUT_SHIFTED,
    screening.REFERENCE_REGIONS_MISSING, screening.TEXT_SPACING_DIFFERS,
    screening.LAYOUT_MODEL_REVIEW,
)
CLASSIFIABLE = {IntakeDocType.TRANSCRIPT_OF_RECORDS.value,
                IntakeDocType.CERTIFICATE_OF_ENROLLMENT.value}


class InvalidRules(ValueError):
    """Reference rules are malformed (HTTP 422)."""


class InvalidLayout(ValueError):
    """The layout block of a reading is malformed (HTTP 422)."""


def school_key(name: str) -> str:
    """The issuing school's name, normalised for matching: case, accents, punctuation, "&",
    a leading "the" and a few abbreviations don't matter."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text.casefold().replace("&", " and "))
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(ABBREVIATIONS.get(w, w) for w in words)[:200]


# ---- rules ------------------------------------------------------------------------------------

def _tolerance(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidRules("layout_tolerance must be a number")
    if not MIN_LAYOUT_TOLERANCE <= value <= MAX_LAYOUT_TOLERANCE:
        raise InvalidRules(f"layout_tolerance must be between {MIN_LAYOUT_TOLERANCE} and "
                           f"{MAX_LAYOUT_TOLERANCE}")
    return float(value)


def parse_rules(raw: Any) -> dict:
    """Validate and normalise a reference's rules. Missing keys get the defaults."""
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise InvalidRules("rules must be a JSON object")
    unknown = set(raw) - RULE_KEYS
    if unknown:
        raise InvalidRules(f"unknown rule(s): {', '.join(sorted(unknown))}")
    required = raw.get("required_fields", list(DEFAULT_REQUIRED_FIELDS))
    if not isinstance(required, list) or len(required) > MAX_RULE_FIELDS or not all(
            isinstance(f, str) and FIELD_NAME.match(f) for f in required):
        raise InvalidRules("required_fields must be a list of field names (lowercase, "
                           f"underscores, at most {MAX_RULE_FIELDS})")
    labels = raw.get("field_labels", {})
    if not isinstance(labels, dict) or len(labels) > MAX_RULE_FIELDS:
        raise InvalidRules("field_labels must be an object: field name -> printed labels")
    clean_labels = {}
    for name, aliases in labels.items():
        if not isinstance(name, str) or not FIELD_NAME.match(name):
            raise InvalidRules(f"field_labels: '{name}' is not a field name")
        if not isinstance(aliases, list) or not 1 <= len(aliases) <= MAX_ALIASES:
            raise InvalidRules(f"field_labels.{name} must list 1 to {MAX_ALIASES} printed labels")
        clean = []
        for alias in aliases:
            if not isinstance(alias, str) or not 1 <= len(alias.strip()) <= MAX_ALIAS_CHARS:
                raise InvalidRules(f"field_labels.{name}: each label must be 1 to "
                                   f"{MAX_ALIAS_CHARS} characters")
            clean.append(alias.strip())
        clean_labels[name] = clean
    return {"required_fields": list(dict.fromkeys(required)), "field_labels": clean_labels,
            "layout_tolerance": _tolerance(raw.get("layout_tolerance",
                                                    DEFAULT_LAYOUT_TOLERANCE))}


# ---- layout measurements ----------------------------------------------------------------------

@dataclass(frozen=True)
class PageLayout:
    page: int
    anchors: dict[str, tuple[float, float]]  # word -> (x, y), page fractions of the top-left
    word_height: float  # median word height / page height
    word_gap: float  # median gap between words on a line / page width
    line_gap: float  # median gap between line tops / page height
    line_count: int


def _unit(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise InvalidLayout(f"{where} must be a number")
    if not 0 <= value <= 1:
        raise InvalidLayout(f"{where} must be between 0 and 1")
    return float(value)


def parse_layout(value: Any) -> tuple[PageLayout, ...] | None:
    """The `layout` block of a reading: {"pages": [{"page", "anchors", "median_word_height",
    "median_word_gap", "median_line_gap", "line_count"}]}. None when it was not sent (an older
    worker): the comparison then says the layout was not measured."""
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("pages"), list):
        raise InvalidLayout("reading.layout.pages must be a list")
    pages = value["pages"]
    if not 1 <= len(pages) <= MAX_LAYOUT_PAGES:
        raise InvalidLayout(f"reading.layout.pages must have 1 to {MAX_LAYOUT_PAGES} pages")
    out = []
    for i, page in enumerate(pages):
        where = f"reading.layout.pages[{i}]"
        if not isinstance(page, dict):
            raise InvalidLayout(f"{where} must be an object")
        anchors = page.get("anchors", {})
        if not isinstance(anchors, dict) or len(anchors) > MAX_ANCHORS:
            raise InvalidLayout(f"{where}.anchors must be an object (at most {MAX_ANCHORS})")
        parsed = {}
        for name, point in anchors.items():
            if not isinstance(name, str) or not ANCHOR_NAME.match(name):
                raise InvalidLayout(f"{where}.anchors: '{name}' is not an anchor word")
            if not isinstance(point, list | tuple) or len(point) != 2:
                raise InvalidLayout(f"{where}.anchors.{name} must be [x, y]")
            parsed[name] = (_unit(point[0], f"{where}.anchors.{name}[0]"),
                            _unit(point[1], f"{where}.anchors.{name}[1]"))
        count = page.get("line_count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 10_000:
            raise InvalidLayout(f"{where}.line_count must be a whole number")
        out.append(PageLayout(
            page=i + 1, anchors=parsed,
            word_height=_unit(page.get("median_word_height", 0), f"{where}.median_word_height"),
            word_gap=_unit(page.get("median_word_gap", 0), f"{where}.median_word_gap"),
            line_gap=_unit(page.get("median_line_gap", 0), f"{where}.median_line_gap"),
            line_count=count))
    return tuple(out)


# ---- references -------------------------------------------------------------------------------

@dataclass(frozen=True)
class RefInfo:
    """What the assessment needs of one approved reference (built from the database row)."""

    id: int
    version: int
    issuing_school: str
    school_key: str
    doc_type: str
    label: str
    use: str
    rules: dict
    layout: tuple[PageLayout, ...] | None
    text: str = ""


def reading_text(reading: dict | None) -> str:
    pages = (reading or {}).get("pages") or []
    return "\n\n".join(str(p.get("ocr_text") or "") for p in pages if isinstance(p, dict))


def ref_info(row: Any) -> RefInfo:
    """From an ApplicantReference row (or any object with the same attributes). A stored
    layout that no longer parses counts as not measured."""
    try:
        layout = parse_layout((row.reading or {}).get("layout"))
    except InvalidLayout:
        layout = None
    try:
        rules = parse_rules(row.rules)
    except InvalidRules:
        rules = parse_rules(None)
    return RefInfo(id=row.id, version=row.version, issuing_school=row.issuing_school,
                   school_key=row.school_key, doc_type=str(row.doc_type), label=str(row.label),
                   use=str(row.use), rules=rules, layout=layout,
                   text=reading_text(row.reading)[:MAX_CORPUS_CHARS])


def pick_reference(refs: Iterable[RefInfo], issuing_school: str,
                   doc_type: str) -> RefInfo | None:
    """The latest (highest version) approved genuine-sample training reference of this school
    and type. `refs` must already be the approved ones."""
    key = school_key(issuing_school)
    candidates = [r for r in refs if r.school_key == key and r.doc_type == str(doc_type)
                  and r.label == ReferenceLabel.GENUINE_SAMPLE
                  and r.use == ReferenceUse.TRAINING]
    return max(candidates, key=lambda r: (r.version, r.id), default=None)


def classifier_corpus(refs: Iterable[RefInfo]) -> list[dict]:
    """Approved references the worker's classifier may use: training ones extend its corpus,
    held-out ones only evaluate it. "Other" samples are the unknown class."""
    out = []
    for r in refs:
        if not r.text.strip():
            continue
        if r.label == ReferenceLabel.OTHER:
            cls = IntakeDocType.UNKNOWN.value
        elif r.doc_type in CLASSIFIABLE:
            cls = r.doc_type
        else:
            continue
        out.append({"id": r.id, "class": cls, "use": r.use, "text": r.text})
    return out


# ---- comparison -------------------------------------------------------------------------------

def compare(layout: tuple[PageLayout, ...] | None, fields: dict, ref: RefInfo) -> list[dict]:
    """Every difference between an upload and its reference, as findings with a reason code."""
    findings: list[dict] = []
    rules = ref.rules
    for name in rules["required_fields"]:
        if not (fields.get(name) or {}).get("value"):
            findings.append({"code": screening.REFERENCE_FIELD_MISSING, "field": name})
    if layout is None or ref.layout is None:
        findings.append({"code": screening.LAYOUT_NOT_MEASURED,
                         "side": "upload" if layout is None else "reference"})
        return findings
    if len(layout) != len(ref.layout):
        findings.append({"code": screening.PAGE_COUNT_DIFFERS, "pages": len(layout),
                         "expected": len(ref.layout)})
    tolerance = rules["layout_tolerance"]
    for page, expected in zip(layout, ref.layout, strict=False):
        for anchor in sorted(set(page.anchors) & set(expected.anchors)):
            distance = math.dist(page.anchors[anchor], expected.anchors[anchor])
            if distance > tolerance:
                findings.append({"code": screening.LAYOUT_SHIFTED, "page": page.page,
                                 "anchor": anchor, "distance": round(distance, 4),
                                 "measured": list(page.anchors[anchor]),
                                 "expected": list(expected.anchors[anchor])})
        missing = sorted(set(expected.anchors) - set(page.anchors))
        if missing:
            findings.append({"code": screening.REFERENCE_REGIONS_MISSING, "page": page.page,
                             "anchors": missing})
        for metric in METRICS:
            value, base = getattr(page, metric), getattr(expected, metric)
            if abs(value - base) / max(base, 0.001) > SPACING_TOLERANCE:
                findings.append({"code": screening.TEXT_SPACING_DIFFERS, "page": page.page,
                                 "metric": metric, "measured": round(value, 5),
                                 "expected": round(base, 5)})
    return findings


def _features(layout: tuple[PageLayout, ...]) -> tuple[float, ...]:
    first = layout[0]
    return (len(layout) / 12, first.line_count / 100, first.word_height * 20,
            first.word_gap * 20, first.line_gap * 20)


def layout_model(layout: tuple[PageLayout, ...] | None, refs: Iterable[RefInfo],
                 issuing_school: str, doc_type: str) -> dict:
    """A 1-nearest-neighbour model over the layout features of the school's labelled samples.

    Unavailable until approved training samples of this school and type are labelled both
    "genuine sample" and "altered sample". Held-out samples never train it; they give its
    evaluation. The verdict is preliminary.
    """
    key = school_key(issuing_school)
    eligible = [r for r in refs if r.school_key == key and r.doc_type == str(doc_type)
                and r.label in (ReferenceLabel.GENUINE_SAMPLE, ReferenceLabel.ALTERED_SAMPLE)
                and r.layout]
    training = sorted((r for r in eligible if r.use == ReferenceUse.TRAINING), key=lambda r: r.id)
    held_out = [r for r in eligible if r.use == ReferenceUse.HELD_OUT]
    if {r.label for r in training} != {ReferenceLabel.GENUINE_SAMPLE,
                                       ReferenceLabel.ALTERED_SAMPLE}:
        return {"status": "unavailable",
                "reason": "needs approved training samples labelled both genuine sample and "
                          "altered sample for this school and type"}
    if not layout:
        return {"status": "unavailable", "reason": "the upload's layout was not measured"}
    points = [(r.id, str(r.label), _features(r.layout)) for r in training]
    version = hashlib.sha256(json.dumps(points).encode()).hexdigest()[:16]

    def classify(features: tuple[float, ...]) -> tuple[str, float]:
        ranked = sorted((math.dist(features, p), label) for _, label, p in points)
        nearest, label = ranked[0]
        opposing = min(d for d, other in ranked if other != label)
        confidence = 1.0 if opposing + nearest == 0 else opposing / (opposing + nearest)
        return label, round(confidence, 4)

    label, confidence = classify(_features(layout))
    hits = sum(classify(_features(r.layout))[0] == r.label for r in held_out)
    return {"status": "evaluated", "preliminary": True, "label": label,
            "confidence": confidence, "version": f"layout-1nn-{version}",
            "training_reference_ids": [r.id for r in training],
            "held_out_count": len(held_out),
            "held_out_accuracy": round(hits / len(held_out), 4) if held_out else None}


def model_flags(model: dict) -> bool:
    """The layout model sends the document to manual review: it placed the upload with the
    altered samples, or it was not confident."""
    return model.get("status") == "evaluated" and (
        model.get("label") != ReferenceLabel.GENUINE_SAMPLE
        or model.get("confidence", 0) < LAYOUT_MODEL_MIN_CONFIDENCE)


@dataclass(frozen=True)
class Assessment:
    reference: RefInfo | None
    findings: list[dict] = field(default_factory=list)
    model: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)  # never NO_REFERENCE_PROFILE

    def as_json(self) -> dict:
        ref = self.reference
        return {
            "reference": None if ref is None else {
                "id": ref.id, "version": ref.version, "issuing_school": ref.issuing_school,
                "doc_type": ref.doc_type, "layout_tolerance": ref.rules["layout_tolerance"],
                "required_fields": ref.rules["required_fields"]},
            "findings": self.findings, "layout_model": self.model, "reasons": self.reasons,
        }


def assess(layout: tuple[PageLayout, ...] | None, fields: dict, issuing_school: str,
           doc_type: str, refs: Sequence[RefInfo]) -> Assessment:
    """Compare an upload with the school's reference and run the layout model."""
    ref = pick_reference(refs, issuing_school, doc_type)
    findings = compare(layout, fields, ref) if ref is not None else []
    model = layout_model(layout, refs, issuing_school, doc_type)
    codes = {f["code"] for f in findings}
    if model_flags(model):
        codes.add(screening.LAYOUT_MODEL_REVIEW)
    return Assessment(reference=ref, findings=findings, model=model,
                      reasons=[c for c in ASSESSMENT_ORDER if c in codes])
