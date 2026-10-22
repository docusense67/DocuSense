"""Stand-in TOR/COE templates and their OCR zones, used until the school's formats arrive.

Zone names equal the snapshot keys they read (student_name -> snapshot["student_name"]),
except `qr_code` and the grade-table regions. Coordinates are page millimetres, derived
from the fixed positions in the HTML: page_x = 20 + x, page_y = 18 + y.
"""

from pathlib import Path

_DIR = Path(__file__).parent

ROW_H = 5.5
VALUE_X, VALUE_W = 56.0, 132.0
FIRST_ROW_Y = 48.0
QR_ZONE = {"x": 164.0, "y": 18.0, "w": 26.0, "h": 26.0}


def _rows(names: list[str]) -> dict:
    return {
        name: {"x": VALUE_X, "y": FIRST_ROW_Y + i * ROW_H, "w": VALUE_W, "h": ROW_H}
        for i, name in enumerate(names)
    }


TOR_ZONES = {
    "unit": "mm",
    "fields": {
        "qr_code": QR_ZONE,
        **_rows(["student_name", "student_number", "program", "document_number",
                 "date_issued", "gwa"]),
        "grades_table": {"x": 20.0, "y": 84.0, "w": 170.0, "h": 191.0, "pages": "first"},
        "grades_table_cont": {"x": 20.0, "y": 18.0, "w": 170.0, "h": 257.0, "pages": "rest"},
    },
}

COE_ZONES = {
    "unit": "mm",
    "fields": {
        "qr_code": QR_ZONE,
        **_rows(["student_name", "student_number", "program", "year_level", "semester",
                 "academic_year", "document_number", "date_issued", "purpose"]),
    },
}


def load_html(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")
