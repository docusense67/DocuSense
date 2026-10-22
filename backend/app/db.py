from collections.abc import Iterator
from typing import Any

from sqlalchemy import JSON, Engine, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# JSONB on Postgres, plain JSON elsewhere (SQLite in tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


def normalize_url(url: str) -> str:
    """Supabase and most hosts hand out `postgres://` / `postgresql://` URLs; this app always
    talks psycopg 3 (the connect options below are psycopg 3 options)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def engine_options(url: str) -> dict[str, Any]:
    """create_engine() keyword arguments for this (normalized) database URL.

    Postgres may sit behind a pooler (Supabase Supavisor): connections are checked before use
    (the pooler or a sleeping free-tier host may have dropped them) and recycled every 30 min,
    and psycopg never prepares statements server-side, so transaction-mode pooling (port
    6543), where consecutive statements can reach different server connections, also works.
    """
    options: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("postgresql+psycopg://"):
        settings = get_settings()
        options.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=1800,
            pool_timeout=30,
            connect_args={"prepare_threshold": None, "connect_timeout": 10,
                          # notice a dead pooler connection instead of hanging on it
                          "keepalives": 1, "keepalives_idle": 30,
                          "keepalives_interval": 10, "keepalives_count": 3},
        )
    return options


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    return create_engine(url, **engine_options(url))


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
