from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_or_404, require_roles
from app.db import get_db
from app.models import User, UserSession
from app.models.enums import Role
from app.schemas import PasswordResetIn, ReasonIn, UserCreateIn, UserOut
from app.security import hash_password
from app.services import audit
from app.services.accounts import check_password_policy, create_account, lock_in_force
from app.timeutil import utcnow

router = APIRouter(prefix="/users", tags=["users"])

staff = require_roles(Role.REGISTRAR, Role.ADMIN)

# Section 4: registrars create students/teachers only; admins manage all accounts.
REGISTRAR_MANAGEABLE = {Role.STUDENT, Role.TEACHER, Role.APPLICANT}


def _ensure_can_manage(actor: User, target_role: Role) -> None:
    if actor.role == Role.REGISTRAR and target_role not in REGISTRAR_MANAGEABLE:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Registrars manage students and teachers only"
        )


@router.get("", response_model=list[UserOut])
def list_users(
    role: Role | None = None, actor: User = Depends(staff), db: Session = Depends(get_db)
):
    query = select(User).order_by(User.id)
    if role:
        query = query.where(User.role == role)
    if actor.role == Role.REGISTRAR:
        query = query.where(User.role.in_(REGISTRAR_MANAGEABLE))
    now = utcnow()
    # "Locked" means locked right now: a lockout that ran out shows as unlocked.
    return [UserOut.model_validate(u).model_copy(update={"is_locked": lock_in_force(u, now)})
            for u in db.scalars(query)]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateIn,
    request: Request,
    actor: User = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """Staff logins (registrar, admin). Student and teacher logins are created with their
    record (POST /students/{id}/account, POST /teachers) so the login is linked to it."""
    if body.role not in {Role.REGISTRAR, Role.ADMIN}:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Create student logins from the student's record and teacher logins when adding "
            "the teacher, so the login is linked to their record",
        )
    user = create_account(db, actor=actor, email=body.email, role=body.role,
                          temporary_password=body.temporary_password,
                          can_validate=body.can_validate, ip_address=client_ip(request))
    db.commit()
    return user


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    user_id: int,
    body: PasswordResetIn,
    request: Request,
    actor: User = Depends(staff),
    db: Session = Depends(get_db),
):
    user = get_or_404(db, User, user_id)
    _ensure_can_manage(actor, user.role)
    check_password_policy(body.temporary_password)

    user.password_hash = hash_password(body.temporary_password)
    user.must_change_password = True
    user.failed_logins = 0
    user.is_locked = False
    user.locked_at = None
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit.record(db, "password_reset", user_id=actor.id, entity_type="user", entity_id=user.id,
                 new_value={"reason": body.reason}, ip_address=client_ip(request))
    db.commit()


@router.post("/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(
    user_id: int,
    body: ReasonIn,
    request: Request,
    actor: User = Depends(staff),
    db: Session = Depends(get_db),
):
    user = get_or_404(db, User, user_id)
    _ensure_can_manage(actor, user.role)
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot deactivate your own account")

    user.is_active = False
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    audit.record(db, "user_deactivated", user_id=actor.id, entity_type="user", entity_id=user.id,
                 new_value={"reason": body.reason}, ip_address=client_ip(request))
    db.commit()
    return user
