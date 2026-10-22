import ipaddress
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import ClassSection, ScanStation, SectionAdviser, User, UserSession
from app.models.enums import Role
from app.security import hash_token
from app.services.accounts import lock_in_force
from app.timeutil import as_utc


def client_ip(request: Request) -> str | None:
    """The caller's address for the audit log.

    ADGVS_TRUSTED_PROXY_HOPS = N says N proxies in front of us each append the address they
    received from to X-Forwarded-For. Entries a client wrote itself sit to the left of those,
    so the client is the N-th entry from the right; everything further left is ignored. With
    N = 0 (default) the header is never read: the address is the connection's peer as the
    server sees it (on-prem, Nginx overwrites X-Forwarded-For and uvicorn applies it)."""
    peer = request.client.host if request.client else None
    hops = get_settings().trusted_proxy_hops
    if hops == 0:
        return peer
    # Several X-Forwarded-For headers count as one comma-separated list (RFC 9110 5.3).
    chain = [part.strip() for value in request.headers.getlist("x-forwarded-for")
             for part in value.split(",") if part.strip()]
    if not chain:
        return peer
    # Fewer entries than hops: the request skipped a proxy; the leftmost is the furthest
    # address a trusted proxy recorded.
    candidate = chain[-hops] if len(chain) >= hops else chain[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer  # garbage (or "unknown") never reaches the audit log


def get_current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    now = datetime.now(UTC)
    if now - as_utc(session.last_seen_at) > timedelta(minutes=settings.session_idle_minutes):
        db.delete(session)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")

    user = session.user
    if not user.is_active or lock_in_force(user, now):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

    # Record activity at most once a minute: reads stay reads, and the 30-minute idle timeout
    # is still accurate to the minute.
    if now - as_utc(session.last_seen_at) > timedelta(seconds=settings.session_touch_seconds):
        session.last_seen_at = now
        db.commit()
    return session


def get_current_user(session: UserSession = Depends(get_current_session)) -> User:
    return session.user


def get_active_user(user: User = Depends(get_current_user)) -> User:
    """Like get_current_user, but blocks everything until a forced password change is done."""
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")
    return user


def require_roles(*roles: Role) -> Callable[..., User]:
    """Server-side role guard for a route (Section 4: checks never live only in the UI)."""

    def guard(user: User = Depends(get_active_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user

    return guard


def require_validator(user: User = Depends(get_active_user)) -> User:
    if user.role != Role.REGISTRAR or not user.can_validate:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Validation operator permission required")
    return user


def get_or_404[T](db: Session, model: type[T], obj_id: int) -> T:
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj


def teacher_profile_id(user: User) -> int:
    if user.teacher is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No teacher profile linked to this account")
    return user.teacher.id


def ensure_section_access(user: User, section: ClassSection) -> None:
    """Row-level rule: teachers only touch their own class sections; registrars see all.
    Every write (grades, submit, change requests) uses this: advising grants no writes."""
    if user.role == Role.REGISTRAR:
        return
    if user.role == Role.TEACHER and section.teacher_id == teacher_profile_id(user):
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your class section")


def advised_by(teacher_id: int):
    """SQL condition: the ClassSection belongs to a block this teacher advises (S-08)."""
    return select(SectionAdviser.id).where(
        SectionAdviser.term_id == ClassSection.term_id,
        SectionAdviser.section_code == ClassSection.section_code,
        SectionAdviser.teacher_id == teacher_id).exists()


def is_adviser(db: Session, teacher_id: int, section: ClassSection) -> bool:
    return db.scalar(select(SectionAdviser.id).where(
        SectionAdviser.term_id == section.term_id,
        SectionAdviser.section_code == section.section_code,
        SectionAdviser.teacher_id == teacher_id)) is not None


def ensure_section_view_access(db: Session, user: User, section: ClassSection) -> None:
    """Read-only rule (S-08): a teacher may VIEW a section they teach or advise. Use only on
    reads (roster, grade sheet); writes keep ensure_section_access."""
    if user.role == Role.TEACHER and is_adviser(db, teacher_profile_id(user), section):
        return
    ensure_section_access(user, section)


def get_station(
    x_station_key: str | None = Header(None, alias="X-Station-Key"),
    db: Session = Depends(get_db),
) -> ScanStation:
    station = x_station_key and db.scalar(
        select(ScanStation).where(ScanStation.api_key_hash == hash_token(x_station_key))
    )
    if not station or not station.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown scan station")
    return station
