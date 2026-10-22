"""Validation decision rules (Sections 8 and 15). Pure functions; no I/O.

Input is what the scan agent read off the paper. Output is VALID / INVALID / NEEDS_REVIEW
with a per-field comparison and plain-language reasons. Guiding rule: an altered critical
field must never come out VALID (Section 21); when unsure, ask a human (NEEDS_REVIEW).
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Literal

from app.models.enums import GeneratedDocumentStatus, ValidationResult

# How the backend matched the paper to an issued document: by its QR token, by the printed
# document number because no QR was read, or by number although a QR was read that matched
# nothing.
Identity = Literal["qr", "number", "foreign_qr"]

VALID, INVALID, REVIEW = (ValidationResult.VALID, ValidationResult.INVALID,
                          ValidationResult.NEEDS_REVIEW)

FOOTER_NOTE = ("This verification confirms that the printed information matches the "
               "institution's records. It does not certify the physical integrity of the paper.")

# field -> (criticality, comparison). Unlisted snapshot keys are not on the paper zones.
FIELD_RULES = {
    "document_number": ("critical", "exact"),
    "student_number": ("critical", "id"),
    "student_name": ("critical", "name"),
    "program": ("important", "text"),
    "date_issued": ("important", "text"),
    "gwa": ("important", "number"),
    "year_level": ("important", "text"),
    "semester": ("important", "text"),
    "academic_year": ("important", "id"),
    "purpose": ("important", "text"),
}


@dataclass
class Thresholds:
    confidence: float = 90.0
    name_similarity: float = 0.90
    # The grading scale (Section 5C). A grade read off the paper that is not on it cannot be
    # printed on a genuine TOR, so it is an OCR misread to review, not proof of tampering.
    grade_scale: tuple[str, ...] = ("1.00", "1.25", "1.50", "1.75", "2.00", "2.25", "2.50",
                                    "2.75", "3.00", "5.00")


@dataclass
class FieldResult:
    field: str
    printed: str | None
    official: str | None
    status: str  # match | mismatch | similar | uncertain | missing
    confidence: float | None
    critical: bool
    # Where on the scan this value was read (page + mm), when the agent reports it.
    zone: dict | None = None


@dataclass
class Outcome:
    result: ValidationResult
    reasons: list[str]
    fields: list[FieldResult] = field(default_factory=list)

    def field_dicts(self) -> list[dict]:
        return [asdict(f) for f in self.fields]


# ---- normalization --------------------------------------------------------------------------

def normalize_text(value: str) -> str:
    """Case, accents (ñ -> n), punctuation and spacing don't count as differences."""
    decomposed = unicodedata.normalize("NFKD", value)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    letters = re.sub(r"[^\w\s]", " ", no_accents.casefold())
    return re.sub(r"\s+", " ", letters).strip()


def normalize_id(value: str) -> str:
    return re.sub(r"[\s\-_.]", "", value).upper()


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        return None


def compare(kind: str, printed: str, official: str) -> tuple[str, float]:
    """Return (status, similarity) for one value pair."""
    if kind == "exact":
        same = printed.strip().upper() == official.strip().upper()
    elif kind == "id":
        same = normalize_id(printed) == normalize_id(official)
    elif kind == "number":
        a, b = _number(printed), _number(official)
        same = a is not None and a == b
    else:  # text, name
        a, b = normalize_text(printed), normalize_text(official)
        if a == b:
            return "match", 1.0
        return "mismatch", SequenceMatcher(None, a, b).ratio()
    return ("match" if same else "mismatch"), (1.0 if same else 0.0)


# ---- Level 2 (basic): page layout -----------------------------------------------------------

# Measured on straightened 300 DPI scans the QR centre lands within ~0.1 mm of its zone and
# the table header within ~1 mm. 8 mm leaves room for real scanners while a re-typeset or
# re-assembled page (QR pasted elsewhere, table moved) stands out.
LAYOUT_TOLERANCE_MM = 8.0


def _centre(box: dict) -> tuple[float, float]:
    return box["x"] + box["w"] / 2, box["y"] + box["h"] / 2


def layout_reasons(pages: list[dict], table: dict | None, template_zones: dict | None,
                   tolerance: float = LAYOUT_TOLERANCE_MM) -> list[str]:
    """Section 8 Level 2 (basic): key anchors of page 1 against the template version the
    document was printed with. Anchors the scan did not measure are not judged here (a
    missing QR already has its own reason). Any difference means NEEDS REVIEW."""
    zones = (template_zones or {}).get("fields", {})
    reasons = []
    qr_box = pages[0].get("qr_box") if pages else None
    if qr_box and "qr_code" in zones:
        zone = zones["qr_code"]
        (qx, qy), (zx, zy) = _centre(qr_box), _centre(zone)
        off = ((qx - zx) ** 2 + (qy - zy) ** 2) ** 0.5
        if off > tolerance:
            reasons.append(f"Page layout differs from the issued template: QR code is "
                           f"{off:.0f} mm from where it is printed")
        elif not 0.5 * zone["w"] <= qr_box["w"] <= 1.1 * zone["w"]:
            reasons.append("Page layout differs from the issued template: QR code is a "
                           "different size than printed")
    header = (table or {}).get("header_box")
    if header and header.get("page") == 1 and "grades_table" in zones:
        off = abs(header["y"] - zones["grades_table"]["y"])
        if off > tolerance:
            reasons.append(f"Page layout differs from the issued template: grades table "
                           f"starts {off:.0f} mm from where it is printed")
    return reasons


# ---- the decision ---------------------------------------------------------------------------

def evaluate(
    *,
    identity: Identity,
    document_found: bool,
    document_status: GeneratedDocumentStatus | None,
    issued_page_count: int | None,
    snapshot: dict | None,
    data_hash: str | None,
    extracted: dict,
    current_records: dict | None,
    thresholds: Thresholds | None = None,
    template_zones: dict | None = None,
) -> Outcome:
    t = thresholds or Thresholds()
    invalid: list[str] = []
    review: list[str] = []
    results: list[FieldResult] = []

    # Level 0: identity.
    if not document_found:
        return Outcome(INVALID, ["Document not issued by this institution (no matching "
                                 "document number or QR code)"])
    if document_status == GeneratedDocumentStatus.REVOKED:
        return Outcome(INVALID, ["Document has been revoked by the registrar"])
    if document_status != GeneratedDocumentStatus.RELEASED:
        return Outcome(INVALID, ["Document was never released by the registrar"])
    if identity == "foreign_qr":
        # A genuine copy's QR always resolves to its own record; this one resolves to none.
        review.append("QR code does not belong to any issued document; document identified "
                      "from the printed number only (possible forgery)")
    elif identity != "qr":
        review.append("QR code unreadable; document identified from the printed number only")

    # Level 2 (basic): pages and footer markers.
    pages = extracted.get("pages", [])
    printed_numbers = sorted(p["footer"]["page"] for p in pages if p.get("footer"))
    expected = list(range(1, (issued_page_count or 1) + 1))
    if len(pages) != len(expected):
        missing = sorted(set(expected) - set(printed_numbers))
        detail = (", ".join(f"page {n} of {len(expected)}" for n in missing) + " missing"
                  if missing else f"{len(pages)} pages scanned, {len(expected)} issued")
        invalid.append(f"Page count differs from issued record: {detail}")
    elif printed_numbers != expected:
        review.append("Page markers unreadable or out of order")
    if pages and not pages[0].get("qr_present"):
        review.append("QR code not found on the first page")

    footer_refs = {p["footer"].get("ref") for p in pages if p.get("footer")} - {None}
    if data_hash and footer_refs and footer_refs != {data_hash[:12]}:
        review.append("Printed reference code does not match the issued record")
    review.extend(layout_reasons(pages, extracted.get("table"), template_zones))

    # Level 1: field-by-field against the snapshot (what was actually printed).
    fields = extracted.get("fields", {})
    for name, (criticality, kind) in FIELD_RULES.items():
        if name not in snapshot:
            continue
        critical = criticality == "critical"
        read = fields.get(name)
        official = str(snapshot[name])
        if not read or not read.get("value"):
            results.append(FieldResult(name, None, official, "missing", None, critical))
            review.append(f"Could not read {_label(name)}")
            continue
        printed, conf = read["value"], float(read.get("confidence", 0))
        status, similarity = compare(kind, printed, official)
        if status == "mismatch" and kind == "name" and similarity >= t.name_similarity:
            status = "similar"
        if status == "match" and conf < t.confidence and critical:
            status = "uncertain"
        results.append(FieldResult(name, printed, official, status, conf, critical))
        _judge(name, status, conf, critical, t, invalid, review)

    if snapshot.get("terms"):
        _compare_table(snapshot["terms"], extracted.get("table") or {}, t, results,
                       invalid, review)

    # Genuine when issued, but the official record changed afterwards (Section 8).
    if current_records is not None:
        ignore = {"document_number", "document_type", "date_issued", "purpose"}
        printed_now = {k: v for k, v in snapshot.items() if k not in ignore}
        today = {k: v for k, v in current_records.items() if k not in ignore}
        if printed_now != today:
            review.append("Official record updated since this document was issued")

    if invalid:
        return Outcome(INVALID, invalid + review, results)
    if review:
        return Outcome(REVIEW, review, results)
    return Outcome(VALID, ["Information matches official records"], results)


def _label(name: str) -> str:
    return name.replace("_", " ")


def _judge(name: str, status: str, conf: float, critical: bool, t: Thresholds,
           invalid: list[str], review: list[str]) -> None:
    label = _label(name)
    label = label[:1].upper() + label[1:]  # not str.capitalize(): keep "IT101" uppercase
    if status == "mismatch":
        if critical and conf >= t.confidence:
            invalid.append(f"{label} does not match official records")
        else:
            review.append(f"{label} differs from official records"
                          + (" (low OCR confidence)" if conf < t.confidence else ""))
    elif status == "similar":
        review.append(f"{label} is similar but not identical to official records")
    elif status == "uncertain":
        review.append(f"{label} read with low OCR confidence")


def _on_scale(grade: str, t: Thresholds) -> bool:
    if grade.strip().upper() in ("INC", "DRP"):
        return True
    value = _number(grade)
    return value is not None and value in {Decimal(g) for g in t.grade_scale}


def _compare_table(terms: list[dict], table: dict, t: Thresholds, results: list[FieldResult],
                   invalid: list[str], review: list[str]) -> None:
    """Match printed rows to issued rows by term and subject code (retakes keep order)."""
    rows = list(table.get("rows", []))
    used = [False] * len(rows)

    for term in terms:
        term_label = f"{term['semester']}, AY {term['academic_year']}"
        for subject in term["subjects"]:
            code = subject["subject_code"]
            index = next((i for i, r in enumerate(rows) if not used[i]
                          and normalize_id(r["subject_code"]) == normalize_id(code)
                          and (r.get("term") in (None, term_label))), None)
            name = f"grade {code} ({term_label})"
            if index is None:
                results.append(FieldResult(name, None, subject["grade"], "missing", None, True))
                review.append(f"Row for {code} ({term_label}) not found on the paper")
                continue
            used[index] = True
            row = rows[index]
            conf = row.get("confidence", {})

            grade_status, _ = compare("number" if _number(subject["grade"]) else "exact",
                                      row["grade"], subject["grade"])
            grade_conf = float(conf.get("grade", 0))
            if grade_status == "match" and grade_conf < t.confidence:
                grade_status = "uncertain"
            off_scale = grade_status == "mismatch" and not _on_scale(row["grade"], t)
            if off_scale:
                grade_status = "uncertain"
            results.append(FieldResult(name, row["grade"], subject["grade"], grade_status,
                                       grade_conf, True, row.get("box")))
            if off_scale:
                review.append(f"Grade for {code} read as {row['grade']}, which is not on the "
                              "grading scale: check the paper")
            else:
                _judge(f"grade for {code}", grade_status, grade_conf, True, t, invalid, review)

            units_status, _ = compare("number", row["units"], subject["units"])
            if units_status != "match":
                units_conf = float(conf.get("units", 0))
                results.append(FieldResult(f"units {code} ({term_label})", row["units"],
                                           subject["units"], units_status, units_conf, False,
                                           row.get("box")))
                _judge(f"units for {code}", units_status, units_conf, False, t, invalid,
                       review)

    extra = [rows[i]["subject_code"] for i, u in enumerate(used) if not u]
    if extra:
        review.append(f"Rows not on the issued document: {', '.join(extra)}")

    printed_gwas = {g.get("term"): g for g in table.get("term_gwas", [])}
    for term in terms:
        term_label = f"{term['semester']}, AY {term['academic_year']}"
        if not term.get("gwa"):
            continue
        read = printed_gwas.get(term_label)
        if read is None:
            continue  # term GWA rows are supporting; the cumulative GWA field is checked
        status, _ = compare("number", read["gwa"], term["gwa"])
        if status != "match":
            results.append(FieldResult(f"term GWA ({term_label})", read["gwa"], term["gwa"],
                                       status, float(read.get("confidence", 0)), False))
            review.append(f"Term GWA for {term_label} differs from official records")
