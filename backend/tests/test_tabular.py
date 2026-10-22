import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from app.services.tabular import TableFormatError, read_table


def xlsx_bytes(rows) -> bytes:
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_reads_xlsx_and_normalizes_cells():
    content = xlsx_bytes([
        ["Student_Number", "Grade", "Birthdate"],
        ["2025-00001", 1.5, datetime(2006, 5, 14)],
        [None, None, None],
        ["2025-00002", 2, None],
    ])
    rows = read_table("grades.XLSX", content, {"student_number", "grade"})
    assert rows[0] == {"student_number": "2025-00001", "grade": "1.5", "birthdate": "2006-05-14"}
    assert rows[1] == {}  # blank row kept so row numbers match the sheet
    assert rows[2]["grade"] == "2"


def test_csv_with_bom_and_short_rows():
    content = "﻿student_number,grade\n2025-00001\n".encode()
    assert read_table("g.csv", content, {"student_number"}) == [
        {"student_number": "2025-00001", "grade": ""}
    ]


@pytest.mark.parametrize(("name", "content", "message"), [
    ("g.txt", b"a", "Upload a .csv or .xlsx"),
    ("g.csv", b"", "empty"),
    ("g.csv", "x\n".encode("utf-16"), "UTF-8"),
    ("g.xlsx", b"not a zip", "Excel"),
])
def test_bad_files(name, content, message):
    with pytest.raises(TableFormatError, match=message):
        read_table(name, content, set())


def _xlsx(build) -> bytes:
    book = Workbook()
    build(book.active)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_xlsx_declaring_a_huge_range_reads_only_the_real_rows():
    """Security review M2: a ~10 KB file declaring A1:ZZ200000 must not exhaust memory."""
    def build(sheet):
        sheet.append(["student_number", "grade"])
        sheet.append(["2025-00001", "1.25"])
        sheet["ZZ200000"] = "x"  # far-away cell: openpyxl pads every row up to it

    content = _xlsx(build)
    assert len(content) < 50_000
    assert read_table("g.xlsx", content, {"student_number", "grade"}) == [
        {"student_number": "2025-00001", "grade": "1.25"}]


def test_too_many_rows_is_refused():
    def build(sheet):
        sheet.append(["student_number", "grade"])
        for i in range(10_001):
            sheet.append([f"2025-{i:05d}", "1.00"])

    with pytest.raises(TableFormatError, match="Too many rows"):
        read_table("g.xlsx", _xlsx(build), {"student_number", "grade"})
