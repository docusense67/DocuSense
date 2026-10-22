"""Synthetic SAMPLE documents for tests and for bootstrapping references.

    python -m scan_agent.screening.synthetic --out .samples --per-type 5 [--tampers]

Renders plausible look-alikes of the four incoming document types (PSA Certificate of Live
Birth, DepEd SF9 JHS report card, Certificate of Good Moral Character, JHS Certificate of
Completion), tampered variants, and out-of-scope documents. Every page carries a diagonal
SAMPLE watermark and only invented names and numbers. Layouts approximate the real forms
from public blank templates; they are NOT the real forms: positions, fonts (Pillow's
bundled Aileron), seals and paper are stand-ins, so accuracy on these pages says nothing
about accuracy on the client's paper (see README "Screening").

Deterministic for a given seed. Pillow (a pytesseract dependency) draws the text.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .profile import PROFILES_DIR

DPI = 300
MM = DPI / 25.4
INK = 28

# tamper -> indicator code it must raise
TAMPERS = {
    "heading_removed": "missing_heading",
    "field_moved": "field_out_of_position",
    "font_mismatch": "font_inconsistent",
    "misaligned_value": "alignment_off",
    "seal_missing": "seal_logo_not_found",
    "pasted_region": "altered_region",
    "added_text": "unexpected_text",
}
UNKNOWN_KINDS = ("receipt", "memo", "recognition", "enrollment_form", "blank")

FIRST = ["JUAN", "MARIA", "JOSE", "ANA", "MARK", "KRISTINE", "JOHN PAUL", "ANGELICA", "RICO",
         "JASMINE", "CARLO", "LEA", "MIGUEL", "PATRICIA", "RENZ", "SOFIA", "PAOLO", "BEA"]
LAST = ["SANTOS", "REYES", "CRUZ", "BAUTISTA", "OCAMPO", "GARCIA", "MENDOZA", "TORRES",
        "AQUINO", "RAMOS", "VILLANUEVA", "CASTILLO", "DE LEON", "NAVARRO", "SALAZAR",
        "PASCUAL", "DIMAANO", "LACSON"]
PLACES = ["QUEZON CITY", "CITY OF MANILA", "CALOOCAN CITY", "PASIG CITY", "BATANGAS CITY",
          "LUCENA CITY", "SAN PABLO CITY", "CITY OF ANTIPOLO"]
PROVINCES = ["METRO MANILA", "BATANGAS", "QUEZON", "LAGUNA", "RIZAL", "CAVITE"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
          "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
SCHOOLS = [  # (name, division, region): invented schools
    ("SAMPLE NATIONAL HIGH SCHOOL", "Schools Division of Batangas City", "Region IV-A CALABARZON"),
    ("MOCK INTEGRATED SCHOOL", "Schools Division of Quezon City", "National Capital Region"),
    ("PLACEHOLDER HIGH SCHOOL", "Schools Division of Laguna", "Region IV-A CALABARZON")]
SUBJECTS = [("filipino", "Filipino"), ("english", "English"), ("mathematics", "Mathematics"),
            ("science", "Science"), ("araling_panlipunan", "Araling Panlipunan (AP)"),
            ("esp", "Edukasyon sa Pagpapakatao (EsP)"),
            ("tle", "Technology and Livelihood Education (TLE)"), ("mapeh", "MAPEH")]


@lru_cache(maxsize=64)
def font(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=px)  # Aileron Regular, bundled with Pillow


def pt_px(pt: float) -> int:
    return round(pt * DPI / 72)


@dataclass
class Txt:
    key: str      # heading:<id> | label:<field> | value:<field> | text:<id>
    text: str
    x: float      # mm, anchor point; y is the baseline
    y: float
    pt: float
    bold: bool = False
    align: str = "left"


@dataclass
class SealMark:
    key: str
    cx: float
    cy: float
    r: float
    style: str     # psa | deped | school
    templated: bool = True


@dataclass
class Layout:
    doc_type: str
    variant: str
    size_mm: tuple[float, float]
    texts: list[Txt] = field(default_factory=list)
    seals: list[SealMark] = field(default_factory=list)
    rules: list[tuple[float, float, float, float]] = field(default_factory=list)
    shades: list[tuple[float, float, float, float]] = field(default_factory=list)
    barcodes: list[tuple[float, float, float, float]] = field(default_factory=list)
    value_boxes: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    # profile-only data
    keywords: list[dict] = field(default_factory=list)
    alignment: list[dict] = field(default_factory=list)
    font_groups: list[dict] = field(default_factory=list)
    free_zones: list[tuple[float, float, float, float]] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    tolerance_mm: float = 4.0
    targets: dict = field(default_factory=dict)  # tamper -> target

    def find(self, key: str) -> Txt:
        for t in self.texts:
            if t.key == key:
                return t
        raise KeyError(key)


# -- fake data ----------------------------------------------------------------------
# render(person=..., lrn=...): the document's own learner (the first name / LRN a layout asks
# for) is replaced, after the random draw, so positions and every other value stay as they
# are for that seed.
_OVERRIDE: ContextVar[dict | None] = ContextVar("synthetic_override", default=None)


def _take(key: str, drawn):
    override = _OVERRIDE.get()
    if override and key in override:
        return override.pop(key)
    return drawn


def _name(rng: random.Random) -> tuple[str, str, str]:
    first = rng.choice(FIRST)
    middle, last = rng.sample(LAST, 2)
    return _take("person", (first, middle, last))


def _date(rng: random.Random, year: int) -> tuple[int, str, int]:
    return rng.randint(1, 28), rng.choice(MONTHS), year


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _lrn(rng: random.Random) -> str:
    return _take("lrn", "".join(str(rng.randint(0, 9)) for _ in range(12)))


# -- layouts ------------------------------------------------------------------------
def _psa(rng: random.Random) -> Layout:
    lay = Layout("psa_birth_certificate", "mf102", (210.0, 297.0), tolerance_mm=3.0)
    t = lay.texts
    child, mother, father = _name(rng), _name(rng), _name(rng)
    mother = (mother[0], mother[1], child[1])  # the child's middle name is the mother's surname
    father = (father[0], father[1], child[2])
    birth_year = rng.randint(2008, 2012)
    d, m, y = _date(rng, birth_year)
    md, mm_, my = _date(rng, birth_year - rng.randint(1, 6))
    city, province = rng.choice(PLACES), rng.choice(PROVINCES)
    t += [Txt("text:form_no", "Municipal Form No. 102", 12, 7.5, 7),
          Txt("text:revised", "(Revised January 2007)", 12, 10.5, 6.5),
          Txt("heading:republic", "Republic of the Philippines", 105, 14, 9, align="center"),
          Txt("heading:psa", "PHILIPPINE STATISTICS AUTHORITY", 105, 20, 11, True, "center"),
          Txt("heading:ocrg", "OFFICE OF THE CIVIL REGISTRAR GENERAL", 105, 25.5, 9,
              align="center"),
          Txt("heading:title", "CERTIFICATE OF LIVE BIRTH", 105, 34, 16, True, "center")]
    lay.seals.append(SealMark("seal:psa_logo", 27, 25, 8.5, "psa"))
    lay.barcodes.append((158, 6, 40, 11))
    lay.rules += [(12, 38, 198, 38), (12, 54, 198, 54), (135, 38, 135, 54)]

    def lv(name, label, lx, ly, value, vx=None, vy=None, box_w=50.0, vpt=10.5):
        vx = lx if vx is None else vx
        vy = ly + 7 if vy is None else vy
        t.append(Txt(f"label:{name}", label, lx, ly, 7))
        t.append(Txt(f"value:{name}", value, vx, vy, vpt))
        lay.value_boxes[name] = (vx - 2, vy - 6, box_w, 8)

    lv("province", "Province", 14, 44, province, vx=32, vy=44, box_w=95)
    lv("city_municipality", "City/Municipality", 14, 51, city, vx=40, vy=51, box_w=90)
    lv("registry_no", "Registry No.", 140, 44, f"{birth_year}-{rng.randint(100, 9999):04d}",
       vy=51, box_w=56)
    lay.shades.append((12, 55, 186, 5))
    t.append(Txt("text:sec_child", "CHILD", 16, 59, 8, True))
    t.append(Txt("label:child_name", "1. NAME", 14, 65, 7))
    for part, x, value in (("first", 40, child[0]), ("middle", 95, child[1]),
                           ("last", 148, child[2])):
        lv(f"child_{part}_name", f"({part.capitalize()})", x, 65, value, box_w=50)
    lay.rules.append((12, 75, 198, 75))
    lv("sex", "2. SEX", 14, 80, rng.choice(["MALE", "FEMALE"]), box_w=44)
    lv("date_of_birth", "3. DATE OF BIRTH", 62, 80, f"{d} {m} {y}", box_w=80)
    lay.rules.append((12, 90, 198, 90))
    lv("place_of_birth", "4. PLACE OF BIRTH", 14, 95,
       f"{rng.choice(['GENERAL HOSPITAL', 'MEDICAL CENTER', 'LYING-IN CLINIC'])}, {city}",
       box_w=184)
    lay.rules.append((12, 105, 198, 105))
    lv("type_of_birth", "5a. TYPE OF BIRTH", 14, 110, "SINGLE", box_w=60)
    lv("birth_order", "5b. BIRTH ORDER", 80, 110, rng.choice(["FIRST", "SECOND", "THIRD"]),
       box_w=55)
    lv("birth_weight", "5c. WEIGHT AT BIRTH", 140, 110, f"{rng.randint(2500, 3900)} GRAMS",
       box_w=56)
    lay.rules.append((12, 120, 198, 120))
    for section, top, label_no, person in (("mother", 121, "7. MAIDEN NAME", mother),
                                           ("father", 157, "14. NAME", father)):
        lay.shades.append((12, top, 186, 5))
        t.append(Txt(f"text:sec_{section}", section.upper(), 16, top + 4, 8, True))
        t.append(Txt(f"label:{section}_name", label_no, 14, top + 10, 7))
        for part, x, value in (("first", 40, person[0]), ("middle", 95, person[1]),
                               ("last", 148, person[2])):
            lv(f"{section}_{part}_name", f"({part.capitalize()})", x, top + 10, value)
        lay.rules.append((12, top + 20, 198, top + 20))
        no = 8 if section == "mother" else 15
        lv(f"{section}_citizenship", f"{no}. CITIZENSHIP", 14, top + 25, "FILIPINO", box_w=55)
        lv(f"{section}_religion", f"{no + 1}. RELIGION", 75, top + 25,
           rng.choice(["ROMAN CATHOLIC", "CHRISTIAN", "ISLAM", "INC"]), box_w=60)
        lv(f"{section}_occupation", f"{no + 4 if section == 'mother' else no + 2}. OCCUPATION",
           140, top + 25, rng.choice(["TEACHER", "ENGINEER", "VENDOR", "DRIVER", "NURSE"]),
           box_w=56)
        lay.rules.append((12, top + 35, 198, top + 35))
    lv("marriage_date", "20a. DATE OF MARRIAGE OF PARENTS", 14, 198, f"{md} {mm_} {my}",
       box_w=82)
    lv("marriage_place", "20b. PLACE OF MARRIAGE OF PARENTS", 100, 198, rng.choice(PLACES),
       box_w=96)
    lay.rules.append((12, 208, 198, 208))
    for i, line in enumerate(["I hereby certify that the information given above is true and",
                              "correct to the best of my knowledge and belief."]):
        t.append(Txt(f"text:cert{i}", line, 14, 215 + i * 5, 7.5))
    t.append(Txt("value:registrar_name", f"{rng.choice(FIRST)} {rng.choice(LAST)}", 150, 240,
                 10, True, "center"))
    lay.value_boxes["registrar_name"] = (110, 234, 80, 8)
    t.append(Txt("label:registrar_name", "Civil Registrar", 150, 245, 7.5, align="center"))
    t.append(Txt("text:remarks", "REMARKS / ANNOTATIONS", 14, 255, 7))
    t.append(Txt("text:footer", "This is a computer-printed copy of the record on file.", 105,
                 282, 7, align="center"))
    lay.free_zones += [(10, 3, 46, 10), (12, 256, 186, 20), (12, 210, 120, 14),
                       (150, 4, 50, 15), (12, 55, 186, 5), (12, 121, 186, 5), (12, 157, 186, 5)]
    lay.keywords = [
        {"text": "CERTIFICATE OF LIVE BIRTH", "weight": 4, "zone": [0.1, 0.0, 0.8, 0.2]},
        {"text": "PHILIPPINE STATISTICS AUTHORITY", "weight": 2, "zone": [0.1, 0.0, 0.8, 0.2]},
        {"text": "CIVIL REGISTRAR", "weight": 2},
        {"text": "Municipal Form No. 102", "weight": 1, "zone": [0.0, 0.0, 0.5, 0.1]},
        {"text": "DATE OF BIRTH", "weight": 1}, {"text": "PLACE OF BIRTH", "weight": 1},
        {"text": "MAIDEN NAME", "weight": 1}, {"text": "Registry No", "weight": 1},
        {"text": "MARRIAGE OF PARENTS", "weight": 1},
        {"text": "Republic of the Philippines", "weight": 0.5}]
    names = lambda who: [f"{who}_{p}_name" for p in ("first", "middle", "last")]  # noqa: E731
    lay.alignment = [
        {"id": "child_name_row", "kind": "baseline", "members": names("child")},
        {"id": "mother_name_row", "kind": "baseline", "members": names("mother")},
        {"id": "father_name_row", "kind": "baseline", "members": names("father")},
        {"id": "birth_row", "kind": "baseline", "members": ["sex", "date_of_birth"]},
        {"id": "birth_type_row", "kind": "baseline",
         "members": ["type_of_birth", "birth_order", "birth_weight"]},
        {"id": "mother_row", "kind": "baseline",
         "members": ["mother_citizenship", "mother_religion", "mother_occupation"]},
        {"id": "father_row", "kind": "baseline",
         "members": ["father_citizenship", "father_religion", "father_occupation"]},
        {"id": "left_labels", "kind": "left", "tolerance_mm": 1.5,
         "members": ["label:sex", "label:place_of_birth", "label:type_of_birth",
                     "label:mother_citizenship", "label:father_citizenship",
                     "label:marriage_date"]}]
    values = [k for k in lay.value_boxes if k != "registrar_name"]
    lay.font_groups = [{"id": "typed_values", "members": values}]
    lay.targets = {"heading_removed": "ocrg", "field_moved": ("date_of_birth", 14, 0),
                   "font_mismatch": "place_of_birth", "misaligned_value": "child_middle_name",
                   "pasted_region": "child_last_name", "added_text": (16, 291)}
    return lay


def _deped_header(lay: Layout, rng: random.Random, cx: float, top: float, school: tuple,
                  title_case: bool = False) -> None:
    name, division, region = school
    lay.texts += [
        Txt("heading:republic", "Republic of the Philippines", cx, top, 9, align="center"),
        Txt("heading:deped", "Department of Education" if title_case else "DEPARTMENT OF EDUCATION",
            cx, top + 6, 11, True, "center"),
        Txt("text:region", region, cx, top + 11.5, 9, align="center"),
        Txt("text:division", division, cx, top + 16.5, 9, align="center"),
        Txt("value:school_name", name, cx, top + 22.5, 11, True, "center")]
    lay.value_boxes["school_name"] = (cx - 62, top + 17.5, 124, 6.5)
    lay.free_zones += [(cx - 80, top + 7, 160, 11)]


def _report_card(rng: random.Random) -> Layout:
    lay = Layout("jhs_report_card", "sf9_single_page", (210.0, 297.0), tolerance_mm=4.0)
    t = lay.texts
    school = rng.choice(SCHOOLS)
    lay.seals += [SealMark("seal:deped_logo", 28, 24, 11, "deped"),
                  SealMark("seal:school_logo", 182, 24, 11, "school", templated=False)]
    t.append(Txt("text:sf9", "SF 9 - JHS", 196, 9, 7, align="right"))
    _deped_header(lay, rng, 105, 14, school)
    t.append(Txt("heading:title", "LEARNER'S PROGRESS REPORT CARD", 105, 48, 14, True, "center"))
    first, middle, last = _name(rng)
    grade = rng.choice(["7", "8", "9", "10"])
    start = rng.randint(2022, 2025)

    def lv(name, label, lx, y, value, vx, box_w):
        t.append(Txt(f"label:{name}", label, lx, y, 10))
        t.append(Txt(f"value:{name}", value, vx, y, 10))
        lay.value_boxes[name] = (vx - 2, y - 6, box_w, 8)
        lay.rules.append((vx - 1, y + 1.2, vx - 2 + box_w, y + 1.2))

    lv("learner_name", "Name:", 15, 62, f"{last}, {first} {middle[0]}.", 32, 100)
    lv("lrn", "LRN:", 138, 62, _lrn(rng), 150, 48)
    lv("age", "Age:", 15, 70, str(rng.randint(12, 17)), 27, 20)
    lv("sex", "Sex:", 55, 70, rng.choice(["Male", "Female"]), 66, 30)
    lv("grade", "Grade:", 100, 70, grade, 114, 18)
    lv("section", "Section:", 138, 70, rng.choice(["RIZAL", "MABINI", "SAMPAGUITA", "NARRA"]),
       155, 43)
    lv("school_year", "School Year:", 15, 78, f"{start}-{start + 1}", 40, 40)
    t.append(Txt("heading:progress", "REPORT ON LEARNING PROGRESS AND ACHIEVEMENT", 105, 92, 10,
                 True, "center"))
    # table: Learning Areas | Quarter 1-4 | Final Rating | Remarks
    x0, x1, top, head_h, row_h = 15.0, 195.0, 96.0, 14.0, 8.0
    rows = SUBJECTS + [("general_average", "General Average")]
    bottom = top + head_h + row_h * len(rows)
    lay.rules += [(x0, top, x1, top), (x0, top + head_h, x1, top + head_h),
                  (x0, bottom, x1, bottom), (90, top + 7, 150, top + 7)]
    for x in (x0, 90, 150, 172, x1):
        lay.rules.append((x, top, x, bottom))
    for x in (105, 120, 135):
        lay.rules.append((x, top + 7, x, bottom - row_h))
    t += [Txt("heading:learning_areas", "Learning Areas", 52.5, top + 9, 9, True, "center"),
          Txt("heading:quarter", "Quarter", 120, top + 5, 9, True, "center"),
          Txt("heading:final_rating", "Final Rating", 161, top + 9, 8, True, "center"),
          Txt("heading:remarks", "Remarks", 183.5, top + 9, 8, True, "center")]
    for i, x in enumerate((97.5, 112.5, 127.5, 142.5)):
        t.append(Txt(f"text:q{i + 1}", str(i + 1), x, top + 12.5, 8, True, "center"))
    finals = []
    for index, (key, label) in enumerate(rows):
        y = top + head_h + index * row_h + 5.8
        lay.rules.append((x0, top + head_h + index * row_h, x1, top + head_h + index * row_h))
        if key == "general_average":
            final = round(sum(finals) / len(finals))
            t.append(Txt(f"label:{key}", label, 17, y, 8.5, True))
        else:
            quarters = [rng.randint(76, 98) for _ in range(4)]
            final = round(sum(quarters) / 4)
            finals.append(final)
            t.append(Txt(f"label:{key}", label, 17, y, 8.5))
            for q, x in zip(quarters, (97.5, 112.5, 127.5, 142.5), strict=True):
                t.append(Txt(f"text:{key}_q", str(q), x, y, 8.5, align="center"))
        t.append(Txt(f"value:{key}", str(final), 161, y, 8.5, align="center"))
        t.append(Txt(f"text:{key}_remarks", "Passed", 183.5, y, 8.5, align="center"))
    lay.tables = [{
        "id": "grades", "tolerance_mm": 4.0,
        "label_x": [17 / 210, 90 / 210], "value_x": [150 / 210, 172 / 210],
        "first_row_y": (top + head_h) / 297, "row_h": row_h / 297,
        "rows": [{"key": k, "label": lbl} for k, lbl in rows],
        "header": ["learning_areas", "quarter", "final_rating", "remarks"]}]
    lines = ["Descriptors          Grading Scale     Remarks",
             "Outstanding               90-100           Passed",
             "Very Satisfactory         85-89            Passed",
             "Satisfactory              80-84            Passed",
             "Fairly Satisfactory       75-79            Passed",
             "Did Not Meet Expectations  Below 75        Failed"]
    for i, line in enumerate(lines):
        t.append(Txt(f"text:scale{i}", line, 30, 195 + i * 5.5, 8, i == 0))
    for key, cx, label in (("adviser_name", 55, "Adviser"), ("principal_name", 150, "Principal")):
        t.append(Txt(f"value:{key}", f"{rng.choice(FIRST)} {rng.choice(LAST)}", cx, 255, 10, True,
                     "center"))
        lay.value_boxes[key] = (cx - 40, 249, 80, 8)
        lay.rules.append((cx - 35, 257, cx + 35, 257))
        t.append(Txt(f"label:{key}", label, cx, 262, 9, align="center"))
    lay.free_zones += [(20, 188, 170, 40), (x0, top, x1 - x0, bottom - top), (180, 4, 20, 8)]
    lay.keywords = [
        {"text": "PROGRESS REPORT CARD", "weight": 3, "zone": [0.1, 0.1, 0.8, 0.12]},
        {"text": "REPORT ON LEARNING PROGRESS AND ACHIEVEMENT", "weight": 2},
        {"text": "General Average", "weight": 2}, {"text": "Final Rating", "weight": 1},
        {"text": "Learning Areas", "weight": 1}, {"text": "Araling Panlipunan", "weight": 1},
        {"text": "Edukasyon sa Pagpapakatao", "weight": 1}, {"text": "SF 9", "weight": 1},
        {"text": "School Year", "weight": 0.5}, {"text": "LRN", "weight": 0.5},
        {"text": "DEPARTMENT OF EDUCATION", "weight": 0.5}]
    lay.alignment = [
        {"id": "name_row", "kind": "baseline",
         "members": ["label:learner_name", "learner_name", "label:lrn", "lrn"]},
        {"id": "info_row", "kind": "baseline",
         "members": ["label:age", "age", "label:sex", "sex", "label:grade", "grade",
                     "label:section", "section"]},
        {"id": "year_row", "kind": "baseline", "members": ["label:school_year", "school_year"]},
        {"id": "info_left", "kind": "left", "tolerance_mm": 1.5,
         "members": ["label:learner_name", "label:age", "label:school_year"]},
        {"id": "signatures", "kind": "baseline", "members": ["adviser_name", "principal_name"]}]
    lay.font_groups = [{"id": "info_values",
                        "members": ["learner_name", "lrn", "age", "sex", "grade", "section",
                                    "school_year", "label:learner_name", "label:lrn",
                                    "label:school_year"]}]
    lay.targets = {"heading_removed": "progress", "field_moved": ("school_year", 22, 0),
                   "font_mismatch": "learner_name", "misaligned_value": "section",
                   "pasted_region": "learner_name", "added_text": (110, 79)}
    return lay


def _good_moral(rng: random.Random) -> Layout:
    school_index = rng.randrange(len(SCHOOLS))
    lay = Layout("good_moral_certificate", "school_letter", (210.0, 297.0), tolerance_mm=6.0)
    t = lay.texts
    shift = (-2.5, 0.0, 2.5)[school_index]  # schools print their own letterheads
    lay.seals += [SealMark("seal:school_logo", 28, 25, 11, "school", templated=False),
                  SealMark("seal:deped_logo", 182, 25, 11, "deped")]
    _deped_header(lay, rng, 105, 15, SCHOOLS[school_index], title_case=True)
    t.append(Txt("text:address", "Sample Street, Poblacion, " + rng.choice(PLACES).title(), 105,
                 42, 8, align="center"))
    lay.rules.append((15, 46, 195, 46))
    first, middle, last = _name(rng)
    start = rng.randint(2022, 2025)
    t += [Txt("heading:title", "CERTIFICATE OF GOOD MORAL CHARACTER", 105, 68 + shift, 15, True,
              "center"),
          Txt("heading:to_whom", "TO WHOM IT MAY CONCERN:", 25, 88 + shift, 10, True),
          Txt("label:student_name", "This is to certify that", 35, 103 + shift, 11),
          Txt("value:student_name", f"{first} {middle[0]}. {last}", 105, 116 + shift, 14, True,
              "center")]
    lay.value_boxes["student_name"] = (25, 109 + shift, 160, 10)
    body = ["a bona fide student of this school, completed Grade 10 (Junior High School)",
            f"in School Year {start}-{start + 1}, and has shown good moral character during",
            "his/her stay in this school. He/She has not been subjected to any",
            "disciplinary action.", "",
            "This certification is issued upon the request of the above-named student",
            "for enrollment purposes."]
    for i, line in enumerate(body):
        if line:
            t.append(Txt(f"text:body{i}", line, 25, 129 + shift + i * 8, 11))
    label = "Issued this"
    t.append(Txt("label:date_issued", label, 35, 191 + shift, 11))
    vx = 35 + font(pt_px(11)).getlength(label + " ") / MM
    d, m, y = _date(rng, start + 1)
    t.append(Txt("value:date_issued", f"{_ordinal(d)} day of {m.title()} {y}", vx, 191 + shift, 11))
    lay.value_boxes["date_issued"] = (vx - 2, 185 + shift, 110, 8)
    lay.seals.append(SealMark("seal:school_seal", 50, 222, 14, "school", templated=False))
    t.append(Txt("value:signatory_name", f"{rng.choice(FIRST)} {rng.choice(LAST)}", 150, 222, 11,
                 True, "center"))
    lay.value_boxes["signatory_name"] = (110, 216, 80, 8)
    t.append(Txt("label:signatory_name", "School Principal", 150, 228, 10, align="center"))
    t.append(Txt("text:not_valid", "Not valid without the school seal.", 25, 252, 8))
    lay.free_zones += [(10, 38, 190, 6)]
    lay.keywords = [
        {"text": "GOOD MORAL CHARACTER", "weight": 4, "zone": [0.05, 0.12, 0.9, 0.2]},
        {"text": "TO WHOM IT MAY CONCERN", "weight": 1},
        {"text": "This is to certify that", "weight": 1},
        {"text": "disciplinary action", "weight": 1},
        {"text": "certification is issued upon the request", "weight": 1},
        {"text": "School Principal", "weight": 0.5},
        {"text": "Department of Education", "weight": 0.5}]
    lay.alignment = [
        {"id": "issued_row", "kind": "baseline", "members": ["label:date_issued", "date_issued"]},
        {"id": "body_left", "kind": "left", "tolerance_mm": 1.5,
         "members": ["label:student_name", "label:date_issued"]}]
    lay.font_groups = [{"id": "body", "members": ["label:student_name", "label:date_issued",
                                                  "date_issued", "heading:to_whom"]}]
    lay.targets = {"heading_removed": "to_whom", "field_moved": ("date_issued", 0, 14),
                   "font_mismatch": "date_issued", "misaligned_value": "date_issued",
                   "pasted_region": "student_name", "added_text": (125, 177)}
    return lay


def _completion(rng: random.Random) -> Layout:
    school_index = rng.randrange(len(SCHOOLS))
    lay = Layout("jhs_completion_certificate", "deped_landscape", (297.0, 210.0),
                 tolerance_mm=6.0)
    t = lay.texts
    cx = 148.5
    shift = (-2.0, 0.0, 2.0)[school_index]
    lay.seals.append(SealMark("seal:deped_logo", cx, 22, 12, "deped"))
    _deped_header(lay, rng, cx, 44, SCHOOLS[school_index])
    first, middle, last = _name(rng)
    year = rng.randint(2023, 2026)
    t += [Txt("heading:title", "CERTIFICATE OF COMPLETION", cx, 86 + shift, 22, True, "center"),
          Txt("heading:awarded", "This certificate is awarded to", cx, 100 + shift, 12,
              align="center"),
          Txt("value:learner_name", f"{first} {middle} {last}", cx, 115 + shift, 20, True,
              "center")]
    lay.value_boxes["learner_name"] = (40, 105 + shift, 217, 13)
    t.append(Txt("label:lrn", "LRN:", 120, 126 + shift, 11))
    t.append(Txt("value:lrn", _lrn(rng), 132, 126 + shift, 11))
    lay.value_boxes["lrn"] = (130, 120 + shift, 55, 8)
    t += [Txt("text:completed", "for having satisfactorily completed the requirements of the", cx,
              138 + shift, 12, align="center"),
          Txt("heading:jhs", "JUNIOR HIGH SCHOOL PROGRAM", cx, 146 + shift, 12, True, "center"),
          Txt("text:prescribed", "prescribed by the Department of Education.", cx, 154 + shift, 12,
              align="center")]
    label = "Given this"
    t.append(Txt("label:date_given", label, 70, 167 + shift, 11))
    vx = 70 + font(pt_px(11)).getlength(label + " ") / MM
    d, m, y = _date(rng, year)
    t.append(Txt("value:date_given", f"{_ordinal(d)} day of {m.title()} {y}", vx, 167 + shift, 11))
    lay.value_boxes["date_given"] = (vx - 2, 161 + shift, 120, 8)
    for key, x, label in (("principal_name", 85, "School Principal"),
                          ("sds_name", 212, "Schools Division Superintendent")):
        t.append(Txt(f"value:{key}", f"{rng.choice(FIRST)} {rng.choice(LAST)}", x, 192, 11, True,
                     "center"))
        lay.value_boxes[key] = (x - 45, 186, 90, 8)
        lay.rules.append((x - 40, 194, x + 40, 194))
        t.append(Txt(f"label:{key}", label, x, 199, 10, align="center"))
    lay.keywords = [
        {"text": "CERTIFICATE OF COMPLETION", "weight": 4, "zone": [0.1, 0.3, 0.8, 0.2]},
        {"text": "JUNIOR HIGH SCHOOL", "weight": 2},
        {"text": "This certificate is awarded to", "weight": 1},
        {"text": "satisfactorily completed", "weight": 1},
        {"text": "Schools Division Superintendent", "weight": 1},
        {"text": "DEPARTMENT OF EDUCATION", "weight": 0.5}, {"text": "LRN", "weight": 0.5},
        {"text": "Given this", "weight": 0.5}]
    lay.alignment = [
        {"id": "given_row", "kind": "baseline", "members": ["label:date_given", "date_given"]},
        {"id": "lrn_row", "kind": "baseline", "members": ["label:lrn", "lrn"]},
        {"id": "signatures", "kind": "baseline", "members": ["principal_name", "sds_name"]}]
    lay.font_groups = [{"id": "body", "members": ["label:date_given", "date_given", "label:lrn",
                                                  "lrn"]}]
    lay.targets = {"heading_removed": "jhs", "field_moved": ("lrn", 26, 0),
                   "font_mismatch": "date_given", "misaligned_value": "lrn",
                   "pasted_region": "learner_name", "added_text": (205, 179)}
    return lay


BUILDERS = {"psa_birth_certificate": _psa, "jhs_report_card": _report_card,
            "good_moral_certificate": _good_moral, "jhs_completion_certificate": _completion}


# -- unknown (out-of-scope) documents -----------------------------------------------
def _unknown(kind: str, rng: random.Random) -> Layout:
    if kind == "receipt":
        lay = Layout("unknown", kind, (80.0, 200.0))
        lines = [("SAMPLE MART CORPORATION", 9, True), ("123 Sample Ave., Quezon City", 7, False),
                 ("VAT REG TIN 000-123-456-000", 7, False), ("OFFICIAL RECEIPT", 9, True)]
        y = 12
        for text, pt, bold in lines:
            lay.texts.append(Txt("text:h", text, 40, y, pt, bold, "center"))
            y += 6
        y += 4
        total = 0.0
        for _ in range(rng.randint(6, 14)):
            price = rng.randint(15, 450) + rng.choice([0, 0.25, 0.5, 0.75])
            total += price
            item = rng.choice(["BREAD", "RICE 5KG", "NOTEBOOK", "BALLPEN", "SOAP", "MILK 1L",
                               "EGGS 12PC", "COFFEE"])
            lay.texts += [Txt("text:i", item, 6, y, 7.5), Txt("text:p", f"{price:,.2f}", 74, y,
                                                               7.5, align="right")]
            y += 5
        y += 3
        lay.texts += [Txt("text:t", "TOTAL", 6, y, 9, True),
                      Txt("text:t", f"{total:,.2f}", 74, y, 9, True, "right"),
                      Txt("text:v", "VATABLE SALES", 6, y + 6, 7),
                      Txt("text:v", f"{total / 1.12:,.2f}", 74, y + 6, 7, align="right"),
                      Txt("text:v", "THIS SERVES AS YOUR OFFICIAL RECEIPT", 40, y + 16, 6.5,
                          align="center")]
        return lay
    if kind == "memo":
        lay = Layout("unknown", kind, (210.0, 297.0))
        _deped_header(lay, rng, 105, 15, rng.choice(SCHOOLS))
        lay.texts += [Txt("text:m", "DIVISION MEMORANDUM", 105, 55, 13, True, "center"),
                      Txt("text:m", f"No. {rng.randint(1, 400)}, s. 2026", 105, 61, 10,
                          align="center")]
        y = 75
        for label, value in (("TO:", "Public Secondary School Heads"),
                             ("FROM:", "Schools Division Superintendent"),
                             ("SUBJECT:", "SCHEDULE OF THE SAMPLE SCHOOL ACTIVITY"),
                             ("DATE:", f"{rng.choice(MONTHS).title()} {rng.randint(1, 28)}, 2026")):
            lay.texts += [Txt("text:l", label, 25, y, 10, True), Txt("text:v", value, 55, y, 10)]
            y += 8
        lay.rules.append((25, y, 185, y))
        words = ("the division office announces the schedule of activities for all secondary "
                 "schools participants are requested to attend and submit the required reports "
                 "on or before the deadline indicated in the enclosure").split()
        y += 12
        for _ in range(rng.randint(8, 14)):
            rng.shuffle(words)
            lay.texts.append(Txt("text:b", " ".join(words[:11]).capitalize() + ".", 25, y, 10.5))
            y += 7
        return lay
    if kind == "recognition":
        lay = Layout("unknown", kind, (297.0, 210.0))
        lay.seals.append(SealMark("seal:deped_logo", 148.5, 22, 12, "deped"))
        _deped_header(lay, rng, 148.5, 44, rng.choice(SCHOOLS))
        first, middle, last = _name(rng)
        lay.texts += [
            Txt("text:t", "CERTIFICATE OF RECOGNITION", 148.5, 86, 22, True, "center"),
            Txt("text:t", "This certificate is awarded to", 148.5, 100, 12, align="center"),
            Txt("text:n", f"{first} {middle} {last}", 148.5, 115, 20, True, "center"),
            Txt("text:t", "for outstanding performance in the Division Science Fair", 148.5, 132,
                12, align="center"),
            Txt("text:t", f"Given this {_ordinal(rng.randint(1, 28))} day of March 2026.", 148.5,
                150, 11, align="center"),
            Txt("text:s", f"{rng.choice(FIRST)} {rng.choice(LAST)}", 212, 192, 11, True, "center"),
            Txt("text:s", "Schools Division Superintendent", 212, 199, 10, align="center")]
        return lay
    if kind == "enrollment_form":
        lay = Layout("unknown", kind, (210.0, 297.0))
        lay.texts += [Txt("text:t", "BASIC EDUCATION ENROLLMENT FORM", 105, 20, 14, True, "center"),
                      Txt("text:t", "THIS FORM IS NOT FOR SALE", 105, 27, 8, align="center"),
                      Txt("text:t", "School Year: 2026 - 2027", 25, 40, 10)]
        y = 55
        for label in ("Learner Reference No. (LRN)", "Last Name", "First Name", "Middle Name",
                      "Birthdate (mm/dd/yyyy)", "Sex", "Mother Tongue", "Current Address",
                      "Father's Name", "Mother's Maiden Name", "Contact Number"):
            lay.texts.append(Txt("text:l", label, 25, y, 9))
            lay.rules.append((85, y + 1, 185, y + 1))
            y += 11
        return lay
    lay = Layout("unknown", "blank", (210.0, 297.0))  # a blank or nearly blank sheet
    lay.texts.append(Txt("text:x", f"{rng.randint(1, 99)}", 190, 285, 9))
    return lay


# -- drawing ------------------------------------------------------------------------
def _anchor(align: str) -> str:
    return {"left": "ls", "center": "ms", "right": "rs"}[align]


def text_box_mm(item: Txt) -> tuple[float, float, float, float]:
    """(x, y, w, h) in mm of the printed string."""
    px = pt_px(item.pt)
    stroke = max(1, round(px / 28)) if item.bold else 0
    left, top, right, bottom = font(px).getbbox(item.text, anchor=_anchor(item.align),
                                                stroke_width=stroke)
    return (item.x + left / MM, item.y + top / MM, (right - left) / MM, (bottom - top) / MM)


def _draw_seal(draw: ImageDraw.ImageDraw, seal: SealMark, ink: int) -> None:
    cx, cy, r = seal.cx * MM, seal.cy * MM, seal.r * MM
    width = max(3, round(r / 25))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ink, width=width)
    inner = r * 0.74
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), outline=ink, width=width)
    ring = {"psa": "PHILIPPINE STATISTICS AUTHORITY *", "deped": "KAGAWARAN NG EDUKASYON *",
            "school": "SAMPLE SCHOOL SEAL * SAMPLE *"}[seal.style]
    small = font(max(10, round(r * 0.16)))
    for i, ch in enumerate(ring):
        angle = -math.pi / 2 + 2 * math.pi * i / len(ring)
        rr = (r + inner) / 2
        draw.text((cx + rr * math.cos(angle), cy + rr * math.sin(angle)), ch, font=small,
                  fill=ink, anchor="mm")
    if seal.style == "psa":
        pts = []
        for i in range(16):
            rad = inner * (0.85 if i % 2 == 0 else 0.4)
            a = -math.pi / 2 + math.pi * i / 8
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        draw.polygon(pts, outline=ink, width=width)
        draw.text((cx, cy), "PSA", font=font(round(inner * 0.45)), fill=ink, anchor="mm")
    elif seal.style == "deped":
        draw.polygon([(cx, cy - inner * 0.8), (cx - inner * 0.5, cy + inner * 0.1),
                      (cx + inner * 0.5, cy + inner * 0.1)], fill=ink)
        for k in range(-3, 4):
            a = -math.pi / 2 + k * 0.28
            top = cy - inner * 0.75
            draw.line((cx + inner * 0.25 * math.cos(a), top + inner * 0.25 * math.sin(a),
                       cx + inner * 0.55 * math.cos(a), top + inner * 0.55 * math.sin(a)),
                      fill=ink, width=width)
        draw.text((cx, cy + inner * 0.5), "DepEd", font=font(round(inner * 0.38)), fill=ink,
                  anchor="mm")
    else:
        draw.rectangle((cx - inner * 0.45, cy - inner * 0.5, cx + inner * 0.45, cy + inner * 0.2),
                       outline=ink, width=width)
        draw.polygon([(cx - inner * 0.45, cy + inner * 0.2), (cx, cy + inner * 0.7),
                      (cx + inner * 0.45, cy + inner * 0.2)], outline=ink, width=width)
        draw.text((cx, cy - inner * 0.15), "S", font=font(round(inner * 0.5)), fill=ink,
                  anchor="mm")


def draw_layout(lay: Layout, paper: int = 238, ink: int = INK) -> np.ndarray:
    w, h = round(lay.size_mm[0] * MM), round(lay.size_mm[1] * MM)
    image = Image.new("L", (w, h), paper)
    draw = ImageDraw.Draw(image)
    for x, y, bw, bh in lay.shades:
        draw.rectangle((x * MM, y * MM, (x + bw) * MM, (y + bh) * MM), fill=paper - 38)
    for x0, y0, x1, y1 in lay.rules:
        draw.line((x0 * MM, y0 * MM, x1 * MM, y1 * MM), fill=ink, width=3)
    for x, y, bw, bh in lay.barcodes:
        bar_rng = random.Random(7)
        cursor = x * MM
        while cursor < (x + bw) * MM:
            width = bar_rng.choice([3, 3, 6, 9])
            draw.rectangle((cursor, y * MM, cursor + width - 1, (y + bh) * MM), fill=ink)
            cursor += width + bar_rng.choice([3, 6, 9])
    for seal in lay.seals:
        _draw_seal(draw, seal, ink)
    for item in lay.texts:
        px = pt_px(item.pt)
        stroke = max(1, round(px / 28)) if item.bold else 0
        draw.text((item.x * MM, item.y * MM), item.text, font=font(px), fill=ink,
                  anchor=_anchor(item.align), stroke_width=stroke, stroke_fill=ink)
    page = np.array(image)
    _watermark(page, paper)
    return page


def _watermark(page: np.ndarray, paper: int) -> None:
    """Light diagonal SAMPLE watermark (thin strokes, 16 levels under the paper: visible to
    people, removed by OCR thresholding)."""
    h, w = page.shape
    layer = np.zeros_like(page)
    scale = w / 260
    size, _ = cv2.getTextSize("SAMPLE", cv2.FONT_HERSHEY_SIMPLEX, scale, 4)
    cv2.putText(layer, "SAMPLE", ((w - size[0]) // 2, (h + size[1]) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, scale, 255, 4, cv2.LINE_AA)
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 35 if h > w else 20, 1.0)
    layer = cv2.warpAffine(layer, matrix, (w, h))
    mask = (layer > 128) & (page > paper - 10)
    page[mask] = paper - 16


def degrade(page: np.ndarray, rng: random.Random, rough: bool = False) -> np.ndarray:
    """What a perspective-corrected camera capture of the printed page looks like: a small
    residual rotation and offset, uneven lighting, sensor noise, slight blur, JPEG.
    `rough`: a poor capture (more blur and noise, washed-out contrast, stronger JPEG)."""
    h, w = page.shape
    angle = rng.uniform(-0.8, 0.8) if rough else rng.uniform(-0.3, 0.3)
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    matrix[:, 2] += (rng.uniform(-1, 1) * MM, rng.uniform(-1, 1) * MM)
    img = cv2.warpAffine(page, matrix, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    gx, gy = rng.uniform(-1, 1), rng.uniform(-1, 1)
    light = 1.0 - rng.uniform(0.0, 0.06) * (0.5 + 0.5 * (gx * (xx / w - 0.5) + gy * (yy / h - 0.5)))
    img = img * light + rng.uniform(-6, 6)
    if rough:
        img = 128 + (img - 128) * rng.uniform(0.7, 0.85)
    noise = 7.0 if rough else 3.0
    img += np.random.default_rng(rng.randint(0, 2**31)).normal(0, noise, img.shape).astype(
        np.float32)
    blur = rng.uniform(1.0, 1.4) if rough else rng.uniform(0.5, 0.8)
    img = cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (0, 0), blur)
    quality = rng.randint(55, 70) if rough else rng.randint(80, 92)
    _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(jpeg, cv2.IMREAD_GRAYSCALE)


# -- samples ------------------------------------------------------------------------
@dataclass
class Sample:
    image: np.ndarray
    doc_type: str          # truth ("unknown" for out-of-scope documents)
    kind: str              # layout variant or unknown kind
    tamper: str | None = None
    expected_indicator: str | None = None
    tamper_box_mm: tuple[float, float, float, float] | None = None
    values: dict[str, str] = field(default_factory=dict)


def _jitter(lay: Layout, rng: random.Random) -> Layout:
    gdx, gdy = rng.uniform(-1.2, 1.2), rng.uniform(-1.2, 1.2)
    texts = [replace(t, x=t.x + gdx + rng.uniform(-0.25, 0.25),
                     y=t.y + gdy + rng.uniform(-0.15, 0.15)) for t in lay.texts]
    # a value and its own row keep a common baseline: re-align to the label's jitter
    seals = [replace(s, cx=s.cx + gdx, cy=s.cy + gdy) for s in lay.seals]
    shift = lambda r: (r[0] + gdx, r[1] + gdy, r[2] + gdx, r[3] + gdy)  # noqa: E731
    return replace(lay, texts=texts, seals=seals, rules=[shift(r) for r in lay.rules],
                   shades=[(x + gdx, y + gdy, w, h) for x, y, w, h in lay.shades],
                   barcodes=[(x + gdx, y + gdy, w, h) for x, y, w, h in lay.barcodes])


def _apply_structural(lay: Layout, tamper: str) -> tuple[Layout, tuple | None]:
    target = lay.targets.get(tamper)
    texts = list(lay.texts)
    box = None
    if tamper == "heading_removed":
        item = lay.find(f"heading:{target}")
        texts.remove(item)
        box = text_box_mm(item)
    elif tamper == "field_moved":
        name, dx, dy = target
        moved = []
        for i, item in enumerate(texts):
            if item.key in (f"label:{name}", f"value:{name}"):
                texts[i] = replace(item, x=item.x + dx, y=item.y + dy)
                moved.append(texts[i])
        box = text_box_mm(moved[0])
    elif tamper == "font_mismatch":
        for i, item in enumerate(texts):
            if item.key == f"value:{target}":
                texts[i] = replace(item, pt=item.pt * 1.5)
                box = text_box_mm(texts[i])
    elif tamper == "misaligned_value":
        for i, item in enumerate(texts):
            if item.key == f"value:{target}":
                texts[i] = replace(item, y=item.y - 2.4)  # raised off its row
                box = text_box_mm(texts[i])
    elif tamper == "seal_missing":
        seals = [s for s in lay.seals if not s.templated]
        gone = [s for s in lay.seals if s.templated]
        box = (gone[0].cx - gone[0].r, gone[0].cy - gone[0].r, 2 * gone[0].r, 2 * gone[0].r)
        return replace(lay, seals=seals), box
    return replace(lay, texts=texts), box


def _apply_pixels(page: np.ndarray, lay: Layout, tamper: str, paper: int,
                  rng: random.Random) -> tuple | None:
    target = lay.targets.get(tamper)
    if tamper == "pasted_region":
        item = lay.find(f"value:{target}")
        x, y, w, h = text_box_mm(item)
        x0, y0 = round((x - 3) * MM), round((y - 2.5) * MM)
        x1, y1 = round((x + w + 3) * MM), round((y + h + 2.5) * MM)
        page[y0:y1, x0:x1] = min(255, paper + 15)  # a whiter slip pasted over the value
        page[y0:y0 + 2, x0:x1] = paper - 30          # its cut edges catch a little shadow
        page[y0:y1, x0:x0 + 2] = paper - 30
        image = Image.fromarray(page)
        new = f"{rng.choice(FIRST)} {rng.choice(LAST)}" if "name" in target else "SAMPLE"
        px = pt_px(item.pt)
        ImageDraw.Draw(image).text((item.x * MM, item.y * MM), new, font=font(px), fill=INK,
                                   anchor=_anchor(item.align),
                                   stroke_width=max(1, round(px / 28)) if item.bold else 0,
                                   stroke_fill=INK)
        page[:] = np.array(image)
        return (x - 3, y - 2.5, w + 6, h + 5)
    if tamper == "added_text":
        x, y = target
        item = Txt("text:added", "WITH HIGHEST HONORS", x, y, 10, True)
        image = Image.fromarray(page)
        px = pt_px(item.pt)
        ImageDraw.Draw(image).text((x * MM, y * MM), item.text, font=font(px), fill=INK,
                                   anchor="ls", stroke_width=1, stroke_fill=INK)
        page[:] = np.array(image)
        return text_box_mm(item)
    return None


def layout(doc_type: str, seed: int = 0) -> Layout:
    """The layout (with fake data) for one synthetic document, before jitter."""
    return BUILDERS[doc_type](random.Random(seed))


def render(doc_type: str, seed: int = 0, tamper: str | None = None,
           clean_capture: bool = False, rough: bool = False,
           person: tuple[str, str, str] | None = None, lrn: str | None = None) -> Sample:
    """One synthetic document. `doc_type` may also be an UNKNOWN_KINDS entry.
    `clean_capture`: the rendered page itself; `rough`: a poor camera capture.
    `person` (first, middle, last) and `lrn`: the learner the document is about (the intake
    demo puts one invented learner on all four documents); default: random for the seed."""
    rng = random.Random(f"{doc_type}:{seed}")
    if doc_type in UNKNOWN_KINDS:
        lay = _unknown(doc_type, rng)
        truth = "unknown"
    else:
        overrides = {k: v for k, v in (("person", person), ("lrn", lrn)) if v is not None}
        token = _OVERRIDE.set(overrides)
        try:
            lay = BUILDERS[doc_type](rng)
        finally:
            _OVERRIDE.reset(token)
        truth = doc_type
    lay = _jitter(lay, rng)
    box = None
    if tamper:
        if tamper not in TAMPERS:
            raise ValueError(f"unknown tamper {tamper!r}")
        lay, box = _apply_structural(lay, tamper)
    paper = rng.randint(232, 244)
    page = draw_layout(lay, paper)
    if tamper:
        box = _apply_pixels(page, lay, tamper, paper, rng) or box
    image = page if clean_capture else degrade(page, rng, rough)
    values = {t.key[6:]: t.text for t in lay.texts if t.key.startswith("value:")}
    return Sample(image, truth, lay.variant if truth != "unknown" else doc_type, tamper,
                  TAMPERS.get(tamper) if tamper else None, box, values)


# -- profile bootstrap ----------------------------------------------------------------
def profile_from_layout(lay: Layout) -> dict:
    """A v1 reference profile from a synthetic layout. The committed profiles were written by
    this function once and are data from then on: edit them (or let enrollment learn the
    real positions) rather than regenerating them."""
    pw, ph = lay.size_mm

    def frac(x, y, w, h, pad=0.0):
        x0, y0 = max(0.0, x - pad), max(0.0, y - pad)
        x1, y1 = min(pw, x + w + pad), min(ph, y + h + pad)
        return [round(x0 / pw, 4), round(y0 / ph, 4), round((x1 - x0) / pw, 4),
                round((y1 - y0) / ph, 4)]

    table_keys = {r["key"] for t in lay.tables for r in t["rows"]}
    table_heads = {h for t in lay.tables for h in t["header"]}
    headings, fields, text_zones = [], [], []
    for item in lay.texts:
        kind, _, name = item.key.partition(":")
        if kind == "heading":
            entry = {"id": name, "text": item.text, "zone": frac(*text_box_mm(item), pad=1.0)}
            if name not in table_heads:
                headings.append(entry)
        elif kind == "text" or (kind == "label" and name not in lay.value_boxes):
            text_zones.append(frac(*text_box_mm(item), pad=2.0))  # e.g. "1. NAME" over parts
    for name, box in lay.value_boxes.items():
        if name in table_keys:
            continue
        entry = {"name": name}
        try:
            label = lay.find(f"label:{name}")
            entry["label"] = label.text
            entry["label_zone"] = frac(*text_box_mm(label), pad=1.0)
        except KeyError:
            entry["missing_value_severity"] = 0.6  # no label: the value IS the field
        entry["value_zone"] = frac(*box)
        fields.append(entry)
    for zone in lay.free_zones:
        text_zones.append(frac(*zone))
    tables = []
    for table in lay.tables:
        out = {k: v for k, v in table.items() if k != "header"}
        out = {k: (round(v, 4) if isinstance(v, float) else
                   [round(x, 4) for x in v] if k in ("label_x", "value_x") else v)
               for k, v in out.items()}
        out["header"] = [{"id": h, "text": lay.find(f"heading:{h}").text,
                          "zone": frac(*text_box_mm(lay.find(f"heading:{h}")), pad=1.0)}
                         for h in table["header"]]
        tables.append(out)
    seals = [{"id": s.key[5:], "template": f"{s.key[5:]}.png",
              "zone": frac(s.cx - s.r, s.cy - s.r, 2 * s.r, 2 * s.r, pad=1.0)}
             for s in lay.seals if s.templated]
    return {
        "schema": 1, "doc_type": lay.doc_type, "variant": lay.variant,
        "description": "v1 bootstrapped from the synthetic layout (an approximation of the real "
                       "form). Re-tune with client samples: see profiles/README.md.",
        "page_mm": list(lay.size_mm), "tolerance_mm": lay.tolerance_mm,
        "keywords": lay.keywords, "headings": headings, "fields": fields, "tables": tables,
        "alignment": lay.alignment, "font_groups": lay.font_groups, "seals": seals,
        "text_zones": text_zones,
        "shaded_zones": [frac(*z, pad=1.0) for z in lay.shades],
    }


def write_profiles(directory: Path = PROFILES_DIR) -> list[Path]:
    paths = []
    for doc_type, builder in BUILDERS.items():
        lay = builder(random.Random(0))
        path = directory / f"{doc_type}.json"
        path.write_text(json.dumps(profile_from_layout(lay), indent=1) + "\n", encoding="utf-8",
                        newline="")
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scan_agent.screening.synthetic")
    parser.add_argument("--out", type=Path, help="write PNGs to <out>/<doc_type>/")
    parser.add_argument("--per-type", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1000)
    parser.add_argument("--tampers", action="store_true", help="also write tampered variants")
    parser.add_argument("--write-profiles", action="store_true",
                        help="(maintainers) regenerate the v1 profiles from the layouts")
    args = parser.parse_args(argv)
    if args.write_profiles:
        for path in write_profiles():
            print(f"wrote {path}")
    if args.out:
        count = 0
        for doc_type in list(BUILDERS) + list(UNKNOWN_KINDS):
            truth = doc_type if doc_type in BUILDERS else "unknown"
            folder = args.out / truth
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(args.per_type):
                sample = render(doc_type, args.seed + i)
                cv2.imwrite(str(folder / f"{doc_type}-{args.seed + i}.png"), sample.image)
                count += 1
                if args.tampers and doc_type in BUILDERS:
                    for tamper in TAMPERS:
                        tampered = render(doc_type, args.seed + i, tamper)
                        tdir = args.out / "tampered" / tamper
                        tdir.mkdir(parents=True, exist_ok=True)
                        cv2.imwrite(str(tdir / f"{doc_type}-{args.seed + i}.png"), tampered.image)
                        count += 1
        print(f"wrote {count} SAMPLE page(s) to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
