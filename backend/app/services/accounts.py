from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.models.enums import Role
from app.security import hash_password
from app.services import audit
from app.timeutil import as_utc


def check_password_policy(password: str) -> None:
    minimum = get_settings().min_password_length
    if len(password) < minimum:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Password must be at least {minimum} characters",
        )


def create_account(
    db: Session,
    *,
    actor: User,
    email: str,
    role: Role,
    temporary_password: str,
    can_validate: bool = False,
    ip_address: str | None = None,
) -> User:
    """Create a login that must change its password on first use. Caller commits."""
    check_password_policy(temporary_password)
    if db.scalar(select(User).where(func.lower(User.email) == email.lower())):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already in use")
    if can_validate and role != Role.REGISTRAR:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Only registrar staff can hold the validation permission")

    user = User(
        email=email.lower(),
        password_hash=hash_password(temporary_password),
        role=role,
        can_validate=can_validate,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    audit.record(db, "user_created", user_id=actor.id, entity_type="user", entity_id=user.id,
                 new_value={"email": user.email, "role": user.role,
                            "can_validate": user.can_validate},
                 ip_address=ip_address)
    return user


def lock_in_force(user: User, now: datetime) -> bool:
    """A lockout lasts lockout_minutes from the last failed attempt that triggered it."""
    if not user.is_locked:
        return False
    if user.locked_at is None:
        return True
    return now - as_utc(user.locked_at) < timedelta(minutes=get_settings().lockout_minutes)
