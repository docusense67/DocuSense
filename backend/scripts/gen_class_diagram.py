"""Generate the Mermaid class diagrams in docs/diagrams/ from the SQLAlchemy models.

    cd backend && .venv/Scripts/python scripts/gen_class_diagram.py [OUT_DIR]

Writes classes-overview.mmd (every table, foreign-key relationships with multiplicities, no
attributes), one classes-<area>.mmd per area (key attributes and enumerations) and refreshes the
generated block of docs/diagrams/README.md. The output is sorted, so a re-run without model
changes gives no diff. tests/test_class_diagram.py fails when the committed files are stale:
re-run this script and commit its output (then `python docs/diagrams/render.py` for SVG/PNG).

Multiplicities come from the foreign keys themselves: the referenced side is 1 (NOT NULL) or
0..1 (nullable); the referencing side is 0..1 when the column is unique, else *.
"""

from __future__ import annotations

import enum
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401  (registers every model on Base.metadata)
from app.db import Base  # noqa: E402

DIAGRAM_DIR = ROOT.parent / "docs" / "diagrams"
README_BEGIN = "<!-- BEGIN generated class diagrams (backend/scripts/gen_class_diagram.py) -->"
README_END = "<!-- END generated class diagrams -->"

# Every table belongs to exactly one area (a new table fails the generator until it is placed).
AREAS: dict[str, tuple[str, tuple[str, ...]]] = {
    "people": ("People and accounts", ("students", "teachers", "user_sessions", "users")),
    "academics": ("Academics: programs, subjects, terms, sections, advisers, grades", (
        "class_sections", "enrollments", "grade_change_requests", "grades", "programs",
        "section_advisers", "subjects", "terms")),
    "documents": ("Requests, generated documents and templates", (
        "document_requests", "document_templates", "generated_documents")),
    "validation": ("Validation of printed documents and scan stations", (
        "scan_sessions", "scan_stations", "scanned_documents", "validation_records")),
    "intake": ("Incoming-document screening", (
        "intake_batches", "intake_documents", "intake_settings")),
    "audit": ("Audit trail", ("audit_logs",)),
}

# Attributes shown besides the automatic ones (primary key, foreign keys, unique columns, enums,
# flags): the business fields a reader needs to follow the workflows.
KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "audit_logs": ("action", "entity_id", "entity_type", "timestamp"),
    "class_sections": ("grades_submitted_at", "section_code"),
    "document_requests": ("copies", "processing_note", "purpose", "requested_at"),
    "document_templates": ("field_zones", "version"),
    "generated_documents": ("data_hash", "data_snapshot", "download_expires_at", "page_count",
                            "released_at"),
    "grade_change_requests": ("new_value", "old_value", "reason"),
    "grades": ("grade_value",),
    "intake_batches": ("ended_at", "manual_review_count", "passed_count", "started_at",
                       "total_docs"),
    "intake_documents": ("assigned_tray", "doc_type_confidence", "reasons", "reviewed_fields",
                         "routed_tray", "screening_result", "sequence"),
    "intake_settings": ("max_indicator_severity", "min_doc_type_confidence", "min_layout_score",
                        "min_similarity_score"),
    "programs": ("name",),
    "scan_sessions": ("ended_at", "started_at", "total_docs"),
    "scan_stations": ("last_seen_at", "last_status"),
    "scanned_documents": ("ocr_confidence", "position_in_stack"),
    "section_advisers": ("section_code",),
    "students": ("first_name", "last_name", "year_level"),
    "subjects": ("title", "units"),
    "teachers": ("full_name",),
    "terms": ("academic_year",),
    "user_sessions": ("last_seen_at",),
    "users": ("last_login_at",),
    "validation_records": ("reasons",),
}

# Layout per area diagram: LR unless that gives a strip too wide to read (exports stay legible).
DIRECTION = {"academics": "TB", "validation": "TB"}

# Columns recording which staff member did something (FK -> users.id other than user_id) are
# left out of the overview to keep it legible; the area diagrams show them.
STAFF_TABLE = "users"


@dataclass(frozen=True, order=True)
class Link:
    child: str  # class name holding the foreign key
    column: str
    parent: str  # referenced class name
    child_mult: str  # how many child rows per parent row
    parent_mult: str  # how many parent rows per child row

    def mermaid(self) -> str:
        return (f'    {self.child} "{self.child_mult}" --> "{self.parent_mult}" {self.parent} : '
                f"{self.column}")


def _mappers() -> dict[str, type]:
    """table name -> mapped class."""
    found = {}
    for mapper in Base.registry.mappers:
        found[mapper.local_table.name] = mapper.class_
    return found


def _type_name(column) -> str:
    t = column.type
    if isinstance(t, Enum) and t.enum_class is not None:
        return t.enum_class.__name__
    for kind, name in ((Boolean, "bool"), (DateTime, "datetime"), (Date, "date"),
                       (Float, "float"), (Numeric, "decimal"), (Integer, "int"),
                       (Text, "text"), (String, "str"), (JSON, "json")):
        if isinstance(t, kind):
            return name
    raise TypeError(f"{column.table.name}.{column.name}: no diagram type for {t!r}")


def _is_unique(column) -> bool:
    if column.unique:
        return True
    for constraint in column.table.constraints:
        if isinstance(constraint, UniqueConstraint) and list(constraint.columns.keys()) == [
                column.name]:
            return True
    return any(ix.unique and list(ix.columns.keys()) == [column.name]
               for ix in column.table.indexes)


def _attributes(table) -> list[str]:
    extras = set(KEY_FIELDS.get(table.name, ()))
    unknown = extras - set(table.columns.keys())
    if unknown:
        raise KeyError(f"KEY_FIELDS[{table.name!r}] names missing columns: {sorted(unknown)}")
    rows = []
    for column in table.columns:
        markers = []
        if column.primary_key:
            markers.append("PK")
        if column.foreign_keys:
            markers.append("FK")
        if _is_unique(column) and not column.primary_key:
            markers.append("UK")
        is_enum = isinstance(column.type, Enum)
        is_flag = isinstance(column.type, Boolean)
        if not (markers or is_enum or is_flag or column.name in extras):
            continue
        # PK first, then FKs, then the rest; alphabetical inside each group.
        rank = 0 if column.primary_key else 1 if column.foreign_keys else 2
        text = f"{_type_name(column)} {column.name}"
        if markers:
            text += " " + ",".join(markers)
        rows.append((rank, column.name, text))
    return [text for _, _, text in sorted(rows)]


def _enums(table) -> list[type[enum.Enum]]:
    return [c.type.enum_class for c in table.columns
            if isinstance(c.type, Enum) and c.type.enum_class is not None]


def _links(classes: dict[str, type]) -> list[Link]:
    links = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            for fk in column.foreign_keys:
                parent = fk.column.table.name
                links.append(Link(
                    child=classes[table.name].__name__,
                    column=column.name,
                    parent=classes[parent].__name__,
                    child_mult="0..1" if _is_unique(column) else "*",
                    parent_mult="0..1" if column.nullable else "1",
                ))
    return sorted(links)


def _check_areas(tables: set[str]) -> None:
    placed = [t for _, members in AREAS.values() for t in members]
    dupes = sorted({t for t in placed if placed.count(t) > 1})
    missing = sorted(tables - set(placed))
    stale = sorted(set(placed) - tables)
    if dupes or missing or stale:
        raise ValueError(f"AREAS out of date: unplaced {missing}, duplicated {dupes}, "
                         f"no such table {stale}")


def _header(title: str) -> list[str]:
    return [
        f"%% {title}",
        "%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.",
        "%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).",
        "classDiagram",
    ]


def _table_label(cls: type) -> str:
    return f'    class {cls.__name__}["{cls.__name__} ({cls.__table__.name})"]'


def overview(classes: dict[str, type], links: list[Link]) -> str:
    lines = _header("Class diagram overview: every table, relationships only")
    lines.append("    direction TB")
    for area, (title, tables) in AREAS.items():
        lines.append(f"    %% {area}: {title}")
        for name in tables:
            lines.append(_table_label(classes[name]))
    staff = classes[STAFF_TABLE].__name__
    kept = [link for link in links if not (link.parent == staff and link.column != "user_id")]
    lines.extend(link.mermaid() for link in kept)
    stamps = sorted({f"{link.child}.{link.column}" for link in links
                     if link.parent == staff and link.column != "user_id"})
    lines.append(f'    note for {staff} "Also referenced by {len(stamps)} staff columns '
                 f'(created_by, reviewed_by, ...),<br>shown in the area diagrams"')
    return "\n".join(lines) + "\n"


def area_diagram(area: str, classes: dict[str, type], links: list[Link]) -> str:
    title, tables = AREAS[area]
    members = {classes[t].__name__ for t in tables}
    lines = _header(f"Class diagram: {title}")
    lines.append(f"    direction {DIRECTION.get(area, 'LR')}")
    enums: dict[str, type[enum.Enum]] = {}
    for name in sorted(tables, key=lambda t: classes[t].__name__):
        cls = classes[name]
        table = cls.__table__
        lines.append(_table_label(cls) + " {")
        lines.extend(f"        {attr}" for attr in _attributes(table))
        lines.append("    }")
        for e in _enums(table):
            enums[e.__name__] = e
    for name, e in sorted(enums.items()):
        lines.append(f"    class {name} {{")
        lines.append("        <<enumeration>>")
        lines.extend(f"        {m.name} = {m.value}" for m in e)
        lines.append("    }")
    own = [link for link in links if link.child in members]
    external = sorted({link.parent for link in own} - members)
    for name in external:
        lines.append(f"    class {name}")
    lines.extend(link.mermaid() for link in own)
    uses = sorted({(classes[t].__name__, e.__name__)
                   for t in tables for e in _enums(classes[t].__table__)})
    lines.extend(f"    {cls} ..> {e}" for cls, e in uses)
    for name in external:
        lines.append(f"    style {name} fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4")
    if area == "audit":
        lines.append('    note for AuditLog "entity_type + entity_id point at any row '
                     '(no foreign key).<br>Append-only: the API login may only INSERT and SELECT."')
    return "\n".join(lines) + "\n"


def render_all() -> dict[str, str]:
    """File name -> Mermaid source, for every class diagram."""
    classes = _mappers()
    _check_areas(set(classes))
    links = _links(classes)
    out = {"classes-overview.mmd": overview(classes, links)}
    for area in AREAS:
        out[f"classes-{area}.mmd"] = area_diagram(area, classes, links)
    return out


def readme_block(diagrams: dict[str, str]) -> str:
    """The generated part of docs/diagrams/README.md: each diagram inline (GitHub renders it)."""
    parts = [README_BEGIN, ""]
    titles = {"classes-overview.mmd": "Overview (relationships only)"}
    titles.update({f"classes-{a}.mmd": t for a, (t, _) in AREAS.items()})
    for name, source in diagrams.items():
        stem = name.removesuffix(".mmd")
        parts += [f"### {titles[name]}", "",
                  f"Source [`{name}`]({name}) · exports [SVG]({stem}.svg), [PNG]({stem}.png)",
                  "", "```mermaid", source.rstrip("\n"), "```", ""]
    parts.append(README_END)
    return "\n".join(parts)


def update_readme(text: str, block: str) -> str:
    start, end = text.index(README_BEGIN), text.index(README_END) + len(README_END)
    return text[:start] + block + text[end:]


def write(out_dir: Path, readme: Path | None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    diagrams = render_all()
    written = []
    for name, source in diagrams.items():
        path = out_dir / name
        path.write_text(source, encoding="utf-8", newline="")
        written.append(path)
    if readme is not None:
        text = readme.read_text(encoding="utf-8")
        readme.write_text(update_readme(text, readme_block(diagrams)), encoding="utf-8",
                          newline="")
        written.append(readme)
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DIAGRAM_DIR
    readme_path = target / "README.md"
    for p in write(target, readme_path if readme_path.exists() else None):
        print(f"Wrote {p}")
