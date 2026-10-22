from datetime import UTC, date, datetime, time, timedelta, timezone

# Philippine time (no daylight saving). Date filters mean school-local calendar days.
PH_TZ = timezone(timedelta(hours=8), "PHT")


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on read; Postgres keeps it. Treat naive values as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def local_day_start(day: date) -> datetime:
    """Start of a Philippine calendar day, in UTC (what both databases compare against)."""
    return datetime.combine(day, time.min, tzinfo=PH_TZ).astimezone(UTC)
