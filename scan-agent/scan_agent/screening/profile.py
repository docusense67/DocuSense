"""Reference profiles: what each incoming document type is expected to look like.

One JSON file per document type and layout variant in `profiles/` (format in
`profiles/README.md`). Zones are fractions of the page width/height ([x, y, w, h]), so they
do not depend on the capture resolution; tolerances are in millimetres of the printed page
(the page is normalized to 300 DPI, so 1 mm = 11.8 px).

Profiles only describe the form. Whether a measured deviation matters is decided by the
backend from the indicator severities (CLAUDE.md: the agent measures, the backend decides).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PROFILES_DIR = Path(__file__).parent / "profiles"
DOC_TYPES = ("psa_birth_certificate", "jhs_report_card", "good_moral_certificate",
             "jhs_completion_certificate")
UNKNOWN = "unknown"

Zone = tuple[float, float, float, float]  # x, y, w, h as fractions of the page width/height


def _opt_zone(value) -> Zone | None:
    return None if value is None else _zone(value)


def _zone(value) -> Zone:
    if value is None or len(value) != 4 or not all(isinstance(v, int | float) for v in value):
        raise ValueError(f"zone must be [x, y, w, h] fractions, got {value!r}")
    x, y, w, h = (float(v) for v in value)
    if not (0 <= x and 0 <= y and 0 < w and 0 < h and x + w <= 1.0001 and y + h <= 1.0001):
        raise ValueError(f"zone outside the page: {value!r}")
    return (x, y, w, h)


@dataclass(frozen=True)
class Keyword:
    """Classification evidence: a phrase, its weight, and optionally where it is expected
    (found elsewhere on the page it counts half)."""
    text: str
    weight: float = 1.0
    zone: Zone | None = None


@dataclass(frozen=True)
class Heading:
    id: str
    text: str
    zone: Zone
    tolerance_mm: float
    missing_severity: float = 0.8


@dataclass(frozen=True)
class FieldSpec:
    """A form field: an optional printed label and the zone its value is printed in. When the
    label is found, the value zone moves with it."""
    name: str
    value_zone: Zone
    tolerance_mm: float
    label: str | None = None
    label_zone: Zone | None = None
    missing_label_severity: float = 0.7
    # Which point of the label is compared with the label zone: its centre, or (for labels
    # printed left-aligned in a wider column, like table rows) its left edge.
    label_anchor: str = "center"
    # A blank or unreadable value is common on genuine forms (unknown father, handwriting):
    # low by default, raise it per field in the profile where a value is always printed.
    missing_value_severity: float = 0.3


@dataclass(frozen=True)
class Group:
    """Members expected to share a measurement: `baseline` / `left` (alignment groups,
    tolerance in mm) or text height (font groups, tolerance as a fraction of the group
    median). Member syntax: `<field>` (its value), `label:<field>`, `heading:<id>`."""
    id: str
    members: tuple[str, ...]
    tolerance: float
    kind: str = "height"


@dataclass(frozen=True)
class Seal:
    id: str
    template: str  # file name, resolved in the reference store, then next to the profile
    zone: Zone
    min_score: float = 0.5
    search_margin_mm: float = 10.0


@dataclass
class Profile:
    doc_type: str
    variant: str
    description: str
    page_mm: tuple[float, float]
    keywords: list[Keyword]
    headings: list[Heading]
    fields: list[FieldSpec]
    alignment: list[Group]
    font_groups: list[Group]
    seals: list[Seal]
    text_zones: list[Zone]          # where any other printed text is normal
    shaded_zones: list[Zone]        # printed tints, ignored by the background-tone check
    unexpected_min_chars: int = 8   # characters of unexpected text = tolerance
    tone_tolerance: float = 6.0     # grey levels of local background change = tolerance
    path: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.doc_type}/{self.variant}"

    def field(self, name: str) -> FieldSpec:
        for spec in self.fields:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def heading(self, ident: str) -> Heading:
        for spec in self.headings:
            if spec.id == ident:
                return spec
        raise KeyError(ident)


def _expand_tables(raw: dict, headings: list[Heading], fields: list[FieldSpec],
                   alignment: list[Group], font_groups: list[Group]) -> None:
    """A table block is shorthand for header headings plus one field per expected row (label
    = the row's printed name, value = the cell in `value_x`)."""
    for table in raw.get("tables", []):
        tol = float(table.get("tolerance_mm", 4.0))
        for head in table.get("header", []):
            headings.append(Heading(head["id"], head["text"], _zone(head["zone"]), tol,
                                    float(head.get("missing_severity", 0.6))))
        lx0, lx1 = table["label_x"]
        vx0, vx1 = table["value_x"]
        y, row_h = float(table["first_row_y"]), float(table["row_h"])
        names = []
        for index, row in enumerate(table["rows"]):
            top = y + index * row_h
            name = row["key"]
            names.append(name)
            fields.append(FieldSpec(
                name=name, label=row["label"], tolerance_mm=tol,
                label_zone=_zone([lx0, top, lx1 - lx0, row_h]),
                value_zone=_zone([vx0, top, vx1 - vx0, row_h]),
                missing_label_severity=float(row.get("missing_label_severity", 0.7)),
                missing_value_severity=float(row.get("missing_value_severity", 0.3)),
                label_anchor="left"))
            alignment.append(Group(f"{table['id']}_row_{name}", (f"label:{name}", name),
                                   float(table.get("baseline_tolerance_mm", 1.2)), "baseline"))
        alignment.append(Group(f"{table['id']}_labels", tuple(f"label:{n}" for n in names),
                               float(table.get("left_tolerance_mm", 1.5)), "left"))
        font_groups.append(Group(f"{table['id']}_values", tuple(names),
                                 float(table.get("font_tolerance", 0.15))))


def parse_profile(raw: dict, path: Path | None = None) -> Profile:
    if raw.get("schema") != 1:
        raise ValueError(f"{path}: unsupported profile schema {raw.get('schema')!r}")
    doc_type = raw["doc_type"]
    if doc_type not in DOC_TYPES:
        raise ValueError(f"{path}: unknown doc_type {doc_type!r}")
    default_tol = float(raw.get("tolerance_mm", 4.0))
    headings = [Heading(h["id"], h["text"], _zone(h["zone"]),
                        float(h.get("tolerance_mm", default_tol)),
                        float(h.get("missing_severity", 0.8))) for h in raw.get("headings", [])]
    fields = [FieldSpec(name=f["name"], label=f.get("label"),
                        label_zone=_opt_zone(f.get("label_zone")),
                        value_zone=_zone(f["value_zone"]),
                        tolerance_mm=float(f.get("tolerance_mm", default_tol)),
                        missing_label_severity=float(f.get("missing_label_severity", 0.7)),
                        missing_value_severity=float(f.get("missing_value_severity", 0.3)),
                        label_anchor=f.get("label_anchor", "center"))
              for f in raw.get("fields", [])]
    for spec in fields:
        if spec.label_anchor not in ("center", "left"):
            raise ValueError(f"{path}: field {spec.name}: label_anchor must be center|left")
    alignment = [Group(g["id"], tuple(g["members"]), float(g.get("tolerance_mm", 1.2)),
                       g["kind"]) for g in raw.get("alignment", [])]
    font_groups = [Group(g["id"], tuple(g["members"]), float(g.get("tolerance", 0.15)))
                   for g in raw.get("font_groups", [])]
    _expand_tables(raw, headings, fields, alignment, font_groups)
    for group in alignment:
        if group.kind not in ("baseline", "left"):
            raise ValueError(f"{path}: alignment group {group.id}: kind must be baseline|left")
    seals = [Seal(s["id"], s["template"], _zone(s["zone"]), float(s.get("min_score", 0.5)),
                  float(s.get("search_margin_mm", 10.0))) for s in raw.get("seals", [])]
    profile = Profile(
        doc_type=doc_type, variant=raw.get("variant", "default"),
        description=raw.get("description", ""),
        page_mm=tuple(raw.get("page_mm", (210.0, 297.0))),
        keywords=[Keyword(k["text"], float(k.get("weight", 1.0)), _opt_zone(k.get("zone")))
                  for k in raw.get("keywords", [])],
        headings=headings, fields=fields, alignment=alignment, font_groups=font_groups,
        seals=seals,
        text_zones=[_zone(z) for z in raw.get("text_zones", [])],
        shaded_zones=[_zone(z) for z in raw.get("shaded_zones", [])],
        unexpected_min_chars=int(raw.get("unexpected_min_chars", 8)),
        tone_tolerance=float(raw.get("tone_tolerance", 6.0)),
        path=path, extra={k: v for k, v in raw.items() if k.startswith("x_")})
    names = [f.name for f in profile.fields]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: duplicate field names")
    known = set(names) | {f"label:{f.name}" for f in profile.fields if f.label} | {
        f"heading:{h.id}" for h in profile.headings}
    for group in profile.alignment + profile.font_groups:
        missing = [m for m in group.members if m not in known]
        if missing:
            raise ValueError(f"{path}: group {group.id} refers to unknown members {missing}")
    if not profile.keywords:
        raise ValueError(f"{path}: a profile needs classification keywords")
    return profile


def load_profiles(directory: Path | str | None = None) -> dict[str, list[Profile]]:
    """{doc_type: [variants]} from every `*.json` profile in `directory` (default: the
    packaged profiles). Cached per directory; call `load_profiles.cache_clear()` after
    editing profiles in a running process."""
    return _load(str(Path(directory) if directory else PROFILES_DIR))


@lru_cache(maxsize=8)
def _load(directory: str) -> dict[str, list[Profile]]:
    profiles: dict[str, list[Profile]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "doc_type" not in raw:
            continue  # calibration.json and other non-profile files
        profile = parse_profile(raw, path)
        profiles.setdefault(profile.doc_type, []).append(profile)
    return profiles


load_profiles.cache_clear = _load.cache_clear  # type: ignore[attr-defined]
