"""Audit trail search (Section 5H: filter and search logs; Section 17). Read-only by design:
there is no route that edits or deletes an entry, and the app's DB login cannot either."""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import AuditLog, User
from app.models.enums import Role
from app.timeutil import local_day_start

router = APIRouter(prefix="/audit-logs", tags=["audit"])
viewer = require_roles(Role.REGISTRAR, Role.ADMIN)


class AuditEntry(BaseModel):
    id: int
    timestamp: datetime
    action: str
    user_email: str | None
    entity_type: str | None
    entity_id: str | None
    old_value: dict | None
    new_value: dict | None
    ip_address: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditEntry]


@router.get("", response_model=AuditPage)
def search_audit_logs(
    q: str | None = Query(None, max_length=100,
                          description="Text in the action, record id, user or values"),
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(viewer),
    db: Session = Depends(get_db),
):
    query = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditLog.entity_id == entity_id)
    if date_from:
        query = query.where(AuditLog.timestamp >= local_day_start(date_from))
    if date_to:
        query = query.where(AuditLog.timestamp < local_day_start(date_to + timedelta(days=1)))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(
            func.lower(AuditLog.action).like(like),
            func.lower(AuditLog.entity_id).like(like),
            func.lower(User.email).like(like),
            func.lower(cast(AuditLog.new_value, String)).like(like),
            func.lower(cast(AuditLog.old_value, String)).like(like),
        ))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    return AuditPage(total=total, items=[
        AuditEntry(id=log.id, timestamp=log.timestamp, action=log.action, user_email=email,
                   entity_type=log.entity_type, entity_id=log.entity_id,
                   old_value=log.old_value, new_value=log.new_value, ip_address=log.ip_address)
        for log, email in rows])
