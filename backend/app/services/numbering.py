"""Sequential, human-readable numbers such as REQ-2026-000123 and TOR-2026-000123."""

from sqlalchemy import select
from sqlalchemy.orm import InstrumentedAttribute, Session


def next_number(db: Session, column: InstrumentedAttribute, prefix: str, width: int = 6) -> str:
    """Highest numeric suffix under `prefix` plus one. Values with a non-numeric suffix
    (imported or legacy numbers) are ignored instead of crashing the sequence. The column's
    unique constraint still guards against concurrent duplicates."""
    candidates = db.scalars(select(column).where(column.like(f"{prefix}%"))
                            .order_by(column.desc()).limit(200))
    highest = 0
    for value in candidates:
        suffix = value[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}{highest + 1:0{width}d}"
