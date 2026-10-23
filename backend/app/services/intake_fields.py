"""Extracted-field review and student matching for incoming documents (S-06/S-07).

Pure functions, no database. The station reads fields as printed (screening_result["fields"]);
staff review and correct them into a reviewed set; from that set the backend suggests matching
students and builds a new student profile. Field values never go into audit entries: callers
log field *names* only (`changed_fields`).

Field keys are the same for every document type where they mean the same thing (last_name,
first_name, ...), so matching and profile creation don't care which document a value came from.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from app.models.enums import IntakeDocType


class UnknownDocType(ValueError):
    """The document type has no field schema (unknown): staff must pick the type first."""


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    # name | person | text | date | lrn | sex | school_year | grade_level | average
    kind: str
    required: bool = False
    student_attr: str | None = None  # Student column this field fills, if any
    sources: tuple[str, ...] = ()  # station field names (joined with a space)
    split: str | None = None  # "last" | "first" | "middle": part of a full name in sources[0]
    max_length: int = 100
    hint: str | None = None


def _names(label: str, *, source: str | None = None, last: str = "", first: str = "",
           middle: str = "") -> tuple[FieldSpec, ...]:
    """Last/first/middle name: from separate station fields, or split from one full name."""
    split = source is not None
    return (
        FieldSpec("last_name", f"{label} last name", "name", True, "last_name",
                  (source or last,), "last" if split else None),
        FieldSpec("first_name", f"{label} first name", "name", True, "first_name",
                  (source or first,), "first" if split else None),
        FieldSpec("middle_name", f"{label} middle name", "name", False, "middle_name",
                  (source or middle,), "middle" if split else None),
    )


DATE_HINT = "YYYY-MM-DD"

SCHEMAS: dict[IntakeDocType, tuple[FieldSpec, ...]] = {
    IntakeDocType.PSA_BIRTH_CERTIFICATE: (
        *_names("Child's", last="child_last_name", first="child_first_name",
                middle="child_middle_name"),
        FieldSpec("sex", "Sex", "sex", False, "sex", ("sex",), hint="Male or Female"),
        FieldSpec("birth_date", "Date of birth", "date", True, "birthdate", ("date_of_birth",),
                  hint=DATE_HINT),
        FieldSpec("birth_place", "Place of birth", "text", False, "birth_place",
                  ("place_of_birth",), max_length=200),
        FieldSpec("mother_name", "Mother's maiden name", "person", False, "mother_name",
                  ("mother_first_name", "mother_middle_name", "mother_last_name"),
                  max_length=200),
        FieldSpec("father_name", "Father's name", "person", False, "father_name",
                  ("father_first_name", "father_middle_name", "father_last_name"),
                  max_length=200),
    ),
    IntakeDocType.JHS_REPORT_CARD: (
        *_names("Learner's", source="learner_name"),
        FieldSpec("lrn", "LRN", "lrn", False, "lrn", ("lrn",), hint="12 digits"),
        FieldSpec("sex", "Sex", "sex", False, "sex", ("sex",), hint="Male or Female"),
        FieldSpec("school_year", "School year", "school_year", False, None, ("school_year",),
                  hint="e.g. 2025-2026"),
        FieldSpec("grade_level", "Grade", "grade_level", False, None, ("grade",),
                  hint="1-12"),
        FieldSpec("section", "Section", "text", False, None, ("section",)),
        FieldSpec("general_average", "General average", "average", False, None,
                  ("general_average",), hint="60-100"),
        FieldSpec("school_name", "School", "text", False, None, ("school_name",),
                  max_length=200),
    ),
    IntakeDocType.GOOD_MORAL_CERTIFICATE: (
        *_names("Student's", source="student_name"),
        FieldSpec("school_name", "School", "text", False, None, ("school_name",),
                  max_length=200),
        FieldSpec("date_issued", "Date issued", "date", False, None, ("date_issued",),
                  hint=DATE_HINT),
    ),
    IntakeDocType.JHS_COMPLETION_CERTIFICATE: (
        *_names("Learner's", source="learner_name"),
        FieldSpec("lrn", "LRN", "lrn", False, "lrn", ("lrn",), hint="12 digits"),
        FieldSpec("school_name", "School", "text", False, None, ("school_name",),
                  max_length=200),
        FieldSpec("date_issued", "Date given", "date", False, None, ("date_given",),
                  hint=DATE_HINT),
    ),
    # From another school, uploaded online (docs/applicant-intake.md). The other school's
    # student number is a source reference only: a new student gets this school's number.
    IntakeDocType.TRANSCRIPT_OF_RECORDS: (
        *_names("Student's", source="student_name"),
        FieldSpec("previous_student_number", "Student number at that school", "text", False,
                  None, ("student_number",), max_length=40),
        FieldSpec("program_name", "Program / course", "text", False, None, ("program",),
                  max_length=200),
        FieldSpec("school_name", "School", "text", False, None, ("school_name",),
                  max_length=200),
        FieldSpec("date_issued", "Date issued", "date", False, None, ("date_issued",),
                  hint=DATE_HINT),
    ),
    IntakeDocType.CERTIFICATE_OF_ENROLLMENT: (
        *_names("Student's", source="student_name"),
        FieldSpec("previous_student_number", "Student number at that school", "text", False,
                  None, ("student_number",), max_length=40),
        FieldSpec("program_name", "Program / course", "text", False, None, ("program",),
                  max_length=200),
        FieldSpec("school_year", "School year", "school_year", False, None, ("school_year",),
                  hint="e.g. 2025-2026"),
        FieldSpec("school_name", "School", "text", False, None, ("school_name",),
                  max_length=200),
        FieldSpec("date_issued", "Date issued", "date", False, None, ("date_issued",),
                  hint=DATE_HINT),
    ),
}


def schema_for(doc_type: IntakeDocType | str) -> tuple[FieldSpec, ...]:
    try:
        return SCHEMAS[IntakeDocType(doc_type)]
    except (KeyError, ValueError):
        raise UnknownDocType("Choose the document type before reviewing its fields") from None


def schema_out(doc_type: IntakeDocType | str) -> list[dict]:
    return [{"key": f.key, "label": f.label, "kind": f.kind, "required": f.required,
             "student_attr": f.student_attr, "hint": f.hint, "max_length": f.max_length}
            for f in schema_for(doc_type)]


# ---- normalization (prefill and save) ------------------------------------------------------

def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y", "%b. %d, %Y",
                "%d %B %Y", "%d %b %Y", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d")
ORDINAL_DATE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+day\s+of\s+([A-Za-z]+),?\s+(\d{4})",
                          re.IGNORECASE)


def parse_printed_date(value: str) -> date | None:
    """A date as printed on Philippine documents ("MARCH 5, 2010", "03/05/2010", "5th day of
    June, 2024", ISO). Numeric dates are read month first, as PSA and DepEd forms print them."""
    text = _squash(value)
    match = ORDINAL_DATE.search(text)
    if match:
        text = f"{match.group(2)} {match.group(1)}, {match.group(3)}"
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize(spec: FieldSpec, value: Any) -> str | None:
    """Tidy a value for its kind. Never invents data: what can't be tidied is returned as is
    (validation then says what is wrong)."""
    if value is None:
        return None
    text = _squash(str(value))
    if not text:
        return None
    if spec.kind == "date":
        parsed = parse_printed_date(text)
        return parsed.isoformat() if parsed else text
    if spec.kind == "lrn":
        digits = re.sub(r"[\s-]", "", text)
        return digits if digits.isdigit() else text
    if spec.kind == "sex":
        return {"M": "Male", "MALE": "Male", "F": "Female", "FEMALE": "Female"}.get(
            text.upper(), text)
    if spec.kind == "school_year":
        match = re.fullmatch(r"(?:S\.?Y\.?\s*)?(\d{4})\s*[-–/]\s*(\d{4})", text, re.IGNORECASE)
        return f"{match.group(1)}-{match.group(2)}" if match else text
    if spec.kind == "grade_level":
        match = re.fullmatch(r"(?:grade\s*)?(\d{1,2})", text, re.IGNORECASE)
        return str(int(match.group(1))) if match else text
    if spec.kind == "average":
        try:
            number = Decimal(text)
        except InvalidOperation:
            return text
        return format(number.normalize(), "f") if number.is_finite() else text
    return text


def split_full_name(full: str) -> dict[str, str | None]:
    """'DELA CRUZ, JUAN P.' -> last / first / middle. Without a comma: 'JUAN P. DELA CRUZ'
    reads first name, middle (one token or an initial) and the rest as the last name. Staff
    check every split value; it is only a starting point."""
    text = _squash(full)
    if not text:
        return {"last": None, "first": None, "middle": None}
    if "," in text:
        last, rest = (part.strip() for part in text.split(",", 1))
        tokens = rest.split()
        if len(tokens) >= 2:
            return {"last": last or None, "first": " ".join(tokens[:-1]), "middle": tokens[-1]}
        return {"last": last or None, "first": rest or None, "middle": None}
    tokens = text.split()
    if len(tokens) == 1:
        return {"last": tokens[0], "first": None, "middle": None}
    if len(tokens) == 2:
        return {"last": tokens[1], "first": tokens[0], "middle": None}
    return {"last": " ".join(tokens[2:]), "first": tokens[0], "middle": tokens[1]}


def _station_value(fields: Mapping, name: str) -> tuple[str | None, float | None]:
    entry = fields.get(name)
    if not isinstance(entry, dict):
        return None, None
    value, confidence = entry.get("value"), entry.get("confidence")
    if value is None or isinstance(value, dict | list):
        value = None
    else:
        value = _squash(str(value)) or None
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        confidence = None
    elif confidence > 1:  # some readers report 0..100
        confidence = confidence / 100 if confidence <= 100 else None
    return value, confidence


def prefill(doc_type: IntakeDocType | str, station_fields: Any) -> dict[str, dict]:
    """What the station read, mapped onto the schema: {key: {value, read_as, confidence,
    derived}}. `value` is normalized, `read_as` is the text as read, `confidence` the lowest of
    the station fields used (None when not read), `derived` = split from a full name."""
    fields = station_fields if isinstance(station_fields, Mapping) else {}
    out = {}
    for spec in schema_for(doc_type):
        reads = [_station_value(fields, name) for name in spec.sources]
        texts = [v for v, _ in reads if v]
        confidences = [c for v, c in reads if v and c is not None]
        read_as = " ".join(texts) or None
        value = read_as
        if spec.split and read_as:
            value = split_full_name(read_as)[spec.split]
        out[spec.key] = {
            "value": normalize(spec, value),
            "read_as": read_as,
            "confidence": min(confidences) if confidences else None,
            "derived": bool(spec.split and read_as),
        }
    return out


# ---- validation -------------------------------------------------------------------------------

NAME = re.compile(r"[^\W\d_](?:[^\W\d_]|[ .'\-])*")
EARLIEST = date(1900, 1, 1)


def _error(spec: FieldSpec, value: str, today: date) -> str | None:
    if len(value) > spec.max_length:
        return f"At most {spec.max_length} characters"
    kind = spec.kind
    if kind in {"name", "person"} and not NAME.fullmatch(value):
        return "Letters, spaces, periods, apostrophes and hyphens only"
    if kind == "date":
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError(value)
            parsed = date.fromisoformat(value)
        except ValueError:
            return "Enter a real date as YYYY-MM-DD"
        if parsed > today:
            return "Date is in the future"
        if parsed < EARLIEST:
            return "Date is before 1900"
    if kind == "lrn" and not re.fullmatch(r"\d{12}", value):
        return "LRN must be exactly 12 digits"
    if kind == "sex" and value not in {"Male", "Female"}:
        return "Male or Female"
    if kind == "school_year":
        match = re.fullmatch(r"(\d{4})-(\d{4})", value)
        if not match or int(match.group(2)) != int(match.group(1)) + 1:
            return "School year as YYYY-YYYY, e.g. 2025-2026"
    if kind == "grade_level" and not (value.isdigit() and 1 <= int(value) <= 12):
        return "Grade must be a whole number from 1 to 12"
    if kind == "average":
        try:
            number = Decimal(value)
        except InvalidOperation:
            return "General average must be a number"
        if not number.is_finite() or not Decimal(60) <= number <= Decimal(100):
            return "General average must be between 60 and 100"
        exponent = number.as_tuple().exponent
        if isinstance(exponent, int) and exponent < -3:
            return "At most 3 decimal places"
    return None


def clean_values(doc_type: IntakeDocType | str, values: Mapping[str, Any],
                 today: date | None = None) -> tuple[dict[str, str | None], dict[str, str]]:
    """Normalize and validate a reviewed field set. Returns (values for every schema key,
    errors by key). Keys outside the schema are errors; missing keys are empty."""
    specs = schema_for(doc_type)
    today = today or date.today()
    known = {s.key for s in specs}
    errors = {key: "Not a field of this document type" for key in values if key not in known}
    cleaned: dict[str, str | None] = {}
    for spec in specs:
        raw = values.get(spec.key)
        if isinstance(raw, bool) or (raw is not None and not isinstance(raw, str | int | float)):
            errors[spec.key] = "Must be text"
            cleaned[spec.key] = None
            continue
        value = normalize(spec, raw)
        cleaned[spec.key] = value
        if value is None:
            if spec.required:
                errors[spec.key] = f"{spec.label} is required"
            continue
        problem = _error(spec, value, today)
        if problem:
            errors[spec.key] = problem
    return cleaned, errors


def changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Keys whose value differs (names only: this is what audit entries record)."""
    keys = sorted(set(before) | set(after))
    return [k for k in keys if (before.get(k) or None) != (after.get(k) or None)]


def student_values(doc_type: IntakeDocType | str, values: Mapping[str, Any]) -> dict[str, Any]:
    """Student column -> value for the reviewed fields that map onto the profile."""
    out: dict[str, Any] = {}
    for spec in schema_for(doc_type):
        value = values.get(spec.key)
        if spec.student_attr and value:
            out[spec.student_attr] = date.fromisoformat(value) if spec.kind == "date" else value
    return out


# ---- candidate matching -----------------------------------------------------------------------

class StudentLike(Protocol):
    id: int
    student_number: str
    first_name: str
    middle_name: str | None
    last_name: str
    birthdate: date | None
    lrn: str | None


def name_key(value: str | None) -> str:
    """Case-, accent-, space- and punctuation-insensitive form ('Dela Cruz' == 'DELACRUZ')."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if c.isalpha()).casefold()


STRONG = {"lrn", "name_birthdate"}
RANK = {"lrn": 0, "name_birthdate": 1, "name": 2, "name_birthdate_differs": 3,
        "first_name_birthdate": 4}


def match_candidates(values: Mapping[str, Any], students: Iterable[StudentLike]) -> list[dict]:
    """Rank existing students against a reviewed field set. Rules, strongest first:

    - `lrn`: same LRN.
    - `name_birthdate`: same last + first name and the same birth date.
    - `name`: same last + first name; one side has no birth date.
    - `name_birthdate_differs`: same last + first name, different birth dates.
    - `first_name_birthdate`: same first name and birth date, different last name (a misread or
      changed surname).

    `conflicts` lists what disagrees (lrn_differs, name_differs, birthdate_differs). Nothing is
    ever linked from here: staff confirm a candidate.
    """
    lrn = values.get("lrn") or None
    last, first = name_key(values.get("last_name")), name_key(values.get("first_name"))
    birth = values.get("birth_date") or None
    birth = date.fromisoformat(birth) if isinstance(birth, str) else birth
    out = []
    for s in students:
        same_name = bool(last and first) and (name_key(s.last_name), name_key(s.first_name)) \
            == (last, first)
        same_lrn = bool(lrn) and s.lrn == lrn
        both_birth = birth is not None and s.birthdate is not None
        same_birth = both_birth and s.birthdate == birth
        if same_lrn:
            match = "lrn"
        elif same_name and same_birth:
            match = "name_birthdate"
        elif same_name and not both_birth:
            match = "name"
        elif same_name:
            match = "name_birthdate_differs"
        elif same_birth and first and name_key(s.first_name) == first:
            match = "first_name_birthdate"
        else:
            continue
        conflicts = []
        if lrn and s.lrn and s.lrn != lrn:
            conflicts.append("lrn_differs")
        if not same_name:
            conflicts.append("name_differs")
        if both_birth and not same_birth:
            conflicts.append("birthdate_differs")
        out.append({
            "student_id": s.id, "student_number": s.student_number,
            "name": ", ".join(p for p in (s.last_name, " ".join(
                p for p in (s.first_name, s.middle_name) if p)) if p),
            "birthdate": s.birthdate.isoformat() if s.birthdate else None, "lrn": s.lrn,
            "match": match, "strength": "strong" if match in STRONG and not conflicts
            else "possible",
            "conflicts": conflicts,
        })
    out.sort(key=lambda c: (RANK[c["match"]], len(c["conflicts"]), c["student_number"]))
    return out
