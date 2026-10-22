from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_session, get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import User, UserSession
from app.schemas import ChangePasswordIn, LoginIn, UserOut
from app.security import hash_password, hash_token, new_token, verify_password
from app.services import audit
from app.services.accounts import check_password_policy, lock_in_force

router = APIRouter(prefix="/auth", tags=["auth"])

def _invalid_credentials() -> HTTPException:
    # One answer for unknown email, wrong password, locked and deactivated accounts, so the
    # login form never confirms that an account exists (security review M3).
    s = get_settings()
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        f"Invalid email or password. After {s.max_failed_logins} failed attempts an account is "
        f"locked for {s.lockout_minutes} minutes; the registrar or an administrator can "
        "unlock it sooner.")


# Checked when the email is unknown, so every login attempt costs one argon2 verification.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    ip = client_ip(request)
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))

    if user is None:
        verify_password(_DUMMY_HASH, body.password)
        audit.record(db, "login_failed", new_value={"email": body.email}, ip_address=ip)
        db.commit()
        raise _invalid_credentials()

    password_ok = verify_password(user.password_hash, body.password)
    if user.is_locked and not lock_in_force(user, now):
        user.is_locked, user.locked_at, user.failed_logins = False, None, 0  # lockout over
        db.flush()  # before the UPDATE below, which counts from the stored value
    if user.is_locked:
        audit.record(db, "login_blocked_locked", user_id=user.id, ip_address=ip)
        db.commit()
        raise _invalid_credentials()

    if not user.is_active or not password_ok:
        # Atomic increment: parallel guesses cannot each read the same count.
        failures = db.execute(update(User).where(User.id == user.id)
                              .values(failed_logins=User.failed_logins + 1)
                              .returning(User.failed_logins)).scalar_one()
        audit.record(db, "login_failed", user_id=user.id, ip_address=ip)
        if failures >= settings.max_failed_logins:
            db.execute(update(User).where(User.id == user.id)
                       .values(is_locked=True, locked_at=now))
            audit.record(db, "account_locked", user_id=user.id, entity_type="user",
                         entity_id=user.id, new_value={"minutes": settings.lockout_minutes},
                         ip_address=ip)
        db.commit()
        raise _invalid_credentials()

    token = new_token()
    db.add(UserSession(token_hash=hash_token(token), user_id=user.id, created_at=now,
                       last_seen_at=now, ip_address=ip))
    user.failed_logins = 0
    user.last_login_at = now
    audit.record(db, "login_success", user_id=user.id, ip_address=ip)
    db.commit()

    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        # No max_age: a browser-session cookie. The server enforces the 30-minute idle
        # timeout, so active users are not logged out 30 minutes after login (review L2).
    )
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: UserSession = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    audit.record(db, "logout", user_id=session.user_id, ip_address=client_ip(request))
    db.delete(db.merge(session))
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordIn,
    request: Request,
    session: UserSession = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    user = db.merge(session.user)
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must differ")
    check_password_policy(body.new_password)

    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    # Sign out every other session for this account.
    db.execute(
        delete(UserSession).where(UserSession.user_id == user.id, UserSession.id != session.id)
    )
    audit.record(db, "password_changed", user_id=user.id, entity_type="user",
                 entity_id=user.id, ip_address=client_ip(request))
    db.commit()
