"""Turn OCR lines into structured values: the page footer and the TOR grades table."""

import re
from dataclasses import asdict, dataclass, field

from scan_agent.processing.ocr import OcrLine, Word, correct

# "TOR-2026-000001 | Page 1 of 2 | Ref 6d95476a59d4". OCR often reads "|" as l, I, ! or 1.
_SEP = r"\s*[|lI!1\]\[]?\s*"
FOOTER_RE = re.compile(
    r"(?P<doc>[A-Z]{2,5}-[A-Z0-9]{4,}-[A-Z0-9]{4,})" + _SEP
    + r"Page\s*(?P<page>\d+)\s*of\s*(?P<pages>\d+)" + _SEP
    + r"(?:Ref\s*(?P<ref>[0-9a-zA-Z]{6,16}))?"
)

GRADE = r"(?:[1-5]\.\d{2}|INC|DRP)"
# Subject codes: letters, then usually digits ("IT101", "NSTP1", "PE 1"), sometimes none
# ("RIZAL"). Upper case only, so a title word ("Filipino") never starts a row.
CODE = r"[A-Z]{2,6}(?:\s?\d{1,4}[A-Z]?)?"
ROW_RE = re.compile(
    r"^(?P<code>" + CODE + r")\s+(?P<title>.+?)\s+"
    r"(?P<units>\d+(?:\.\d+)?)\s+(?P<grade>" + GRADE + r")\s+(?P<remarks>[A-Za-z]+)\s*$"
)
ROW_START = re.compile(r"^" + CODE + r"\s+\S")
TERM_RE = re.compile(
    # "1st" is often read "lst"/"Ist": the ordinal format leaves only one meaning.
    r"(?P<semester>(?:[1lI|!]st|2nd)\s*Semester|Summer)\s*,?\s*AY\s*"
    r"(?P<y1>\d{4})\s*-?\s*(?P<y2>\d{4})",  # OCR sometimes drops the dash
    re.I)
TERM_GWA_RE = re.compile(r"Term\s*GWA\s*:?\s*(?P<gwa>\d\.\d{2})", re.I)
HEADER_RE = re.compile(r"^Code\s+Descriptive", re.I)
SEMESTERS = {"1st semester": "1st Semester", "2nd semester": "2nd Semester", "summer": "Summer"}


@dataclass
class Footer:
    document_number: str
    page: int
    pages: int
    ref: str | None
    confidence: float


def parse_footer(line: OcrLine) -> Footer | None:
    match = FOOTER_RE.search(line.text)
    if not match:
        return None
    ref = correct("ref", match["ref"]) if match["ref"] else None
    return Footer(document_number=match["doc"], page=int(match["page"]),
                  pages=int(match["pages"]), ref=ref, confidence=line.confidence)


@dataclass
class TableRow:
    term: str | None
    subject_code: str
    subject_title: str
    units: str
    grade: str
    remarks: str
    confidence: dict[str, float]
    # Where the row sits: region pixels from the parser, converted by the pipeline to
    # page millimetres {"page", "x", "y", "w", "h"} so the dashboard can crop the row.
    box: dict | None = None


@dataclass
class TableExtract:
    rows: list[TableRow] = field(default_factory=list)
    term_gwas: list[dict] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    # The column header row ("Code  Descriptive Title ..."): where the table starts, for
    # the Level 2 layout check. Region pixels from the parser, page mm after the pipeline.
    header_box: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _word_conf(line: OcrLine, token: str) -> float:
    for word in reversed(line.words):
        if word.text == token:
            return word.confidence
    return line.confidence


_JUNK = re.compile(r"^[|~_=\-'`,.:;!\[\]]+$")
_EDGE = "|~_[]!'`"


def clean_line(line: OcrLine) -> OcrLine:
    """Drop tokens made of border remnants ("|", "~") and strip them from word edges."""
    words = []
    for w in line.words:
        text = w.text.strip(_EDGE)
        if text and not _JUNK.match(text):
            words.append(Word(text, w.confidence, w.left, w.top, w.width, w.height))
    return OcrLine(words)


# Glyph confusions Tesseract makes on table cells. Applied only where the column's format
# makes the reading unambiguous; they never change which digits were printed, so an altered
# grade still reads as altered.
_DIGIT_LOOKALIKES = str.maketrans({"l": "1", "I": "1", "L": "1", "i": "1", "|": "1", "!": "1",
                                   "O": "0", "o": "0", "D": "0", "S": "5", "s": "5",
                                   ":": ".", ",": ".", ";": "."})
_LETTER_LOOKALIKES = str.maketrans({"1": "I", "0": "O", "5": "S", "8": "B", "2": "Z", "$": "S"})
_GRADE_FORMAT = re.compile(r"^[1-5]\.\d{2}$")
_CODE_FORMAT = re.compile(r"^(?P<lead>[0-9]{1,2})(?P<rest>[A-Z]{1,5}\d{1,4}[A-Z]?)$")
# One letter, then a digit lookalike where the code's second letter must be ("C5204").
_CODE_SECOND = re.compile(r"^(?P<first>[A-Z])(?P<second>[0-9$])(?P<rest>\d{1,4}[A-Z]?)$")
# Letters (any case, "$" for S) then the digits: a subject code read in mixed case ("Cs101").
_CODE_ANY_CASE = re.compile(r"^[A-Za-z$]{2,6}\d{1,4}[A-Za-z]?$")
# Letters, then a digit run whose inner characters were read as letters ("FIL1O1").
_CODE_INNER = re.compile(
    r"^(?P<letters>[A-Z]{2,6})(?P<digits>\d[0-9OoIl|]{0,2}\d)(?P<suffix>[A-Z]?)$")
_INNER_DIGITS = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})


def fix_grade(token: str) -> str:
    """'1:75' -> '1.75', '125' -> '1.25', 'L25' -> '1.25', '2.75.' -> '2.75'. Grades are
    always d.dd (or INC/DRP), so a grade with its point dropped or misread, or a speck read
    as a trailing point, has only one meaning."""
    if token.upper() in ("INC", "DRP"):
        return token.upper()
    fixed = token.translate(_DIGIT_LOOKALIKES)
    if re.fullmatch(r"[1-5]\d{2}", fixed):
        fixed = f"{fixed[0]}.{fixed[1:]}"
    elif re.fullmatch(r"[1-5]\.\d{2}\.", fixed):
        fixed = fixed[:-1]
    return fixed if _GRADE_FORMAT.match(fixed) else token


def fix_code(token: str) -> str:
    """'1T101' -> 'IT101', 'C5204' -> 'CS204', 'Cs101' -> 'CS101', 'FIL1O1' -> 'FIL101'.
    Subject codes are upper-case letters (at least two) then digits, so a digit where a
    letter must be is a misread letter, a letter between two digits is a misread digit,
    and case carries no meaning (validation compares codes case-insensitively). Only a
    character in a position the format reserves for the other kind is ever changed."""
    if _CODE_ANY_CASE.match(token) and not token.isupper():
        token = token.replace("$", "S").upper()
    if m := _CODE_FORMAT.match(token):
        return m["lead"].translate(_LETTER_LOOKALIKES) + m["rest"]
    if m := _CODE_SECOND.match(token):
        return m["first"] + m["second"].translate(_LETTER_LOOKALIKES) + m["rest"]
    if m := _CODE_INNER.match(token):
        return m["letters"] + m["digits"].translate(_INNER_DIGITS) + m["suffix"]
    return token


def _join_split_grade(words: list[Word]) -> list[Word]:
    """'3 1 00 Passed' -> '3 1.00 Passed': a grade whose point was read as a gap. Only
    when a units number still precedes it, so the two pieces can only be the grade."""
    if len(words) < 6:
        return words
    units, whole, cents = words[-4], words[-3], words[-2]
    if not (re.fullmatch(r"[1-5]", whole.text)
            and re.fullmatch(r"\d{2}", cents.text.translate(_DIGIT_LOOKALIKES))
            and re.fullmatch(r"\d+(?:\.\d+)?", units.text)):
        return words
    right = max(whole.left + whole.width, cents.left + cents.width)
    bottom = max(whole.top + whole.height, cents.top + cents.height)
    top = min(whole.top, cents.top)
    joined = Word(f"{whole.text}.{cents.text}", min(whole.confidence, cents.confidence),
                  whole.left, top, right - whole.left, bottom - top)
    return [*words[:-3], joined, words[-1]]


def fix_row_tokens(line: OcrLine) -> OcrLine:
    """Correct the code (first word) and grade (second to last) of a probable table row."""
    words = list(line.words)
    if len(words) < 5 or not words[-1].text.isalpha():
        return line
    words = _join_split_grade(words)
    for index, fix in ((0, fix_code), (-2, fix_grade)):
        w = words[index]
        words[index] = Word(fix(w.text), w.confidence, w.left, w.top, w.width, w.height)
    return OcrLine(words)


def parse_table(lines: list[OcrLine], current_term: str | None = None) -> TableExtract:
    """Rows keep the term they appear under. A term can continue onto the next page, so
    callers pass the last term seen on the previous page as `current_term`."""
    out = TableExtract()
    term = current_term
    # A long subject title wraps inside its cell; OCR then splits the row over 2-3 lines
    # (code + first words, the numbers, the rest of the title). Hold a row start until the
    # lines after it complete it.
    pending: list[OcrLine] = []

    def flush() -> None:
        out.unparsed.extend(p.text.strip() for p in pending)
        pending.clear()

    for raw in lines:
        line = clean_line(raw)
        text = line.text.strip()
        if not text:
            continue
        if HEADER_RE.match(text):
            flush()
            if out.header_box is None and line.box is not None:
                out.header_box = dict(zip(("left", "top", "right", "bottom"), line.box,
                                          strict=True))
            continue
        if m := TERM_RE.search(text):
            flush()
            semester = re.sub(r"\s*semester", " semester", m["semester"].lower())
            semester = SEMESTERS[re.sub(r"^[li|!](?=st)", "1", semester)]
            term = f"{semester}, AY {m['y1']}-{m['y2']}"
            continue
        if m := TERM_GWA_RE.search(text):
            flush()
            out.term_gwas.append({"term": term, "gwa": m["gwa"],
                                  "confidence": _word_conf(line, m["gwa"])})
            continue
        if row := _row(line, term):
            flush()
            out.rows.append(row)
            continue
        if pending:
            joined = OcrLine([w for p in pending for w in p.words] + line.words)
            if row := _row(joined, term):
                pending.clear()
                out.rows.append(row)
                continue
            if len(pending) < 3 and not ROW_START.match(fix_row_tokens(line).text):
                pending.append(line)  # more of the same row
                continue
            flush()
        if ROW_START.match(fix_row_tokens(line).text):
            pending.append(line)
            continue
        out.unparsed.append(text)
    flush()
    return out


def _row(line: OcrLine, term: str | None) -> TableRow | None:
    line = fix_row_tokens(line)
    m = ROW_RE.match(line.text.strip())
    if not m:
        return None
    box = line.box
    return TableRow(
        box=None if box is None else dict(zip(("left", "top", "right", "bottom"), box,
                                              strict=True)),
        term=term, subject_code=m["code"].replace(" ", ""), subject_title=m["title"],
        units=m["units"], grade=m["grade"], remarks=m["remarks"],
        confidence={"subject_code": _word_conf(line, m["code"].split()[0]),
                    "units": _word_conf(line, m["units"]),
                    "grade": _word_conf(line, m["grade"])},
    )
