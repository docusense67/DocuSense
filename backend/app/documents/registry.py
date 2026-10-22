"""Document types the system can issue. Adding one (e.g. COG) = one entry here plus a
DocumentType enum value, a context builder, a template and its zones.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.config import get_settings
from app.documents import context, defaults
from app.models import Student
from app.models.enums import DocumentType

# Section 13 placeholder catalog: the only names a template may use.
PLACEHOLDER_CATALOG: dict[str, str] = {
    "student_name": "First name, middle initial, last name, suffix (uppercase)",
    "student_number": "Student number",
    "program": "Program name",
    "year_level": "e.g. 3rd Year",
    "semester": "e.g. 1st Semester",
    "academic_year": "e.g. 2025-2026",
    "date_issued": "e.g. October 5, 2026",
    "document_number": "e.g. TOR-2026-000123",
    "qr_code": "Verification QR image (use as <img src>)",
    "gwa": "Cumulative GWA (TOR)",
    "purpose": "Purpose given in the request",
    "terms": "TOR terms: each has semester, academic_year, gwa, subjects[] "
             "(subject_code, subject_title, units, grade, remarks)",
    "data_hash_short": "First 12 characters of the data fingerprint",
    "institution_name": "School name",
    "institution_address": "School address",
    "registrar_name": "Signatory name",
    "registrar_title": "Signatory title",
}

# Values supplied by the system rather than the student's records.
SYSTEM_PLACEHOLDERS = {"document_number", "qr_code", "data_hash_short", "institution_name",
                       "institution_address", "registrar_name", "registrar_title"}


@dataclass(frozen=True)
class DocumentTypeSpec:
    code: DocumentType
    label: str
    required_placeholders: frozenset[str]
    required_zones: frozenset[str]
    build_context: Callable[[Session, Student, context.BuildOptions], dict]
    sample_context: Callable[[], dict]
    default_html: str
    default_zones: dict


def institution_context() -> dict:
    s = get_settings()
    return {
        "institution_name": s.institution_name,
        "institution_address": s.institution_address,
        "registrar_name": s.registrar_name,
        "registrar_title": s.registrar_title,
    }


def _sample_common() -> dict:
    return {
        "student_name": "JUAN D. DELA CRUZ JR.",
        "student_number": "2021-00123",
        "program": "Bachelor of Science in Information Technology",
        "date_issued": context.format_date(date(2026, 9, 25)),
    }


def _sample_tor() -> dict:
    subjects = [
        ("IT101", "Introduction to Computing", "3", "1.25", "Passed"),
        ("IT102", "Computer Programming 1", "3", "1.50", "Passed"),
        ("GE101", "Understanding the Self", "3", "1.75", "Passed"),
    ]
    rows = [dict(zip(("subject_code", "subject_title", "units", "grade", "remarks"), s,
                     strict=True)) for s in subjects]
    return {
        **_sample_common(),
        "gwa": "1.50",
        "terms": [
            {"semester": "1st Semester", "academic_year": "2025-2026", "gwa": "1.50",
             "subjects": rows},
            {"semester": "2nd Semester", "academic_year": "2025-2026", "gwa": "1.50",
             "subjects": rows},
        ],
    }


def _sample_coe() -> dict:
    return {
        **_sample_common(),
        "year_level": "3rd Year",
        "semester": "1st Semester",
        "academic_year": "2026-2027",
        "purpose": "Employment",
    }


_COMMON_REQUIRED = {"student_name", "student_number", "program", "date_issued",
                    "document_number", "qr_code"}

REGISTRY: dict[DocumentType, DocumentTypeSpec] = {
    DocumentType.TOR: DocumentTypeSpec(
        code=DocumentType.TOR,
        label="Transcript of Records",
        required_placeholders=frozenset(_COMMON_REQUIRED | {"terms", "gwa"}),
        required_zones=frozenset({"qr_code", "student_name", "student_number", "program",
                                  "document_number", "date_issued", "gwa", "grades_table"}),
        build_context=context.build_tor,
        sample_context=_sample_tor,
        default_html="tor.html",
        default_zones=defaults.TOR_ZONES,
    ),
    DocumentType.COE: DocumentTypeSpec(
        code=DocumentType.COE,
        label="Certificate of Enrollment",
        required_placeholders=frozenset(_COMMON_REQUIRED | {"year_level", "semester",
                                                            "academic_year", "purpose"}),
        required_zones=frozenset({"qr_code", "student_name", "student_number", "program",
                                  "year_level", "semester", "academic_year",
                                  "document_number", "date_issued"}),
        build_context=context.build_coe,
        sample_context=_sample_coe,
        default_html="coe.html",
        default_zones=defaults.COE_ZONES,
    ),
}
