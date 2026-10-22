"""Registrar dashboard aggregates (Section 16). One call per page load."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.db import get_db
from app.models import (
    AuditLog,
    ClassSection,
    DocumentRequest,
    Enrollment,
    GeneratedDocument,
    Grade,
    ScannedDocument,
    Student,
    Term,
    User,
    ValidationRecord,
)
from app.models.enums import (
    EnrollmentStatus,
    GradeRemark,
    RequestStatus,
    Role,
    StudentStatus,
    ValidationResult,
)
from app.services.scanning import scanned_number
from app.timeutil import PH_TZ, as_utc, local_day_start, utcnow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
# Section 4: registrar full view; system admin read-only.
viewer = require_roles(Role.REGISTRAR, Role.ADMIN)

R = RequestStatus
# Cards use the words students see (S-11): Pending / Approved / Completed / Rejected.
PENDING = {R.REQUESTED}
APPROVED = {R.APPROVED, R.FOR_VERIFICATION, R.VERIFIED}
RELEASED = {R.RELEASED, R.EXPIRED}


def _months(count: int) -> list[str]:
    now = utcnow()
    year, month = now.year, now.month
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def _request_cards(db: Session, date_range: tuple[datetime, datetime] | None) -> dict:
    now = utcnow()
    today = local_day_start(now.astimezone(PH_TZ).date())
    periods = {"today": (today, now), "week": (now - timedelta(days=7), now),
               "month": (now - timedelta(days=30), now)}
    if date_range:
        periods["range"] = date_range
    cards = {p: Counter() for p in periods}
    for status_, requested_at in db.execute(
            select(DocumentRequest.status, DocumentRequest.requested_at)):
        when = as_utc(requested_at)
        for period, (start, end) in periods.items():
            if not start <= when < end:
                continue
            bucket = cards[period]
            bucket["total"] += 1
            if status_ in PENDING:
                bucket["pending"] += 1
            elif status_ in APPROVED:
                bucket["approved"] += 1
            elif status_ in RELEASED:
                bucket["released"] += 1
            elif status_ == R.REJECTED:
                bucket["rejected"] += 1
    keys = ("total", "pending", "approved", "released", "rejected")
    return {p: {k: c[k] for k in keys} for p, c in cards.items()}


def _data_health(db: Session) -> list[dict]:
    """Students whose records will block a TOR request (Section 16, 'Data health')."""
    issues: dict[int, list[str]] = defaultdict(list)
    locked_terms = select(Term.id).where(Term.is_grades_locked.is_(True))
    rows = db.execute(
        select(Enrollment.student_id, Grade.id, Grade.remarks)
        .join(ClassSection, ClassSection.id == Enrollment.class_section_id)
        .outerjoin(Grade, Grade.enrollment_id == Enrollment.id)
        .where(ClassSection.term_id.in_(locked_terms),
               Enrollment.status == EnrollmentStatus.ENROLLED)
    )
    missing, incomplete = Counter(), Counter()
    for student_id, grade_id, remarks in rows:
        if grade_id is None:
            missing[student_id] += 1
        elif remarks == GradeRemark.INC:
            incomplete[student_id] += 1
    for student_id, n in missing.items():
        issues[student_id].append(f"{n} missing grade(s) in locked terms")
    for student_id, n in incomplete.items():
        issues[student_id].append(f"{n} INC grade(s)")
    for (student_id,) in db.execute(select(Student.id).where(
            Student.program_id.is_(None), Student.status != StudentStatus.INACTIVE)):
        issues[student_id].append("No program on record")
    if not issues:
        return []
    students = {s.id: s for s in db.scalars(select(Student).where(Student.id.in_(issues)))}
    return [{"student_id": sid, "student_number": students[sid].student_number,
             "name": f"{students[sid].last_name}, {students[sid].first_name}",
             "issues": issues[sid]} for sid in sorted(issues)][:50]


@router.get("/registrar")
def registrar_dashboard(date_from: date | None = None, date_to: date | None = None,
                        _: User = Depends(viewer), db: Session = Depends(get_db)):
    """date_from/date_to (school-local days, inclusive) add a 'range' request card and limit
    the validation summary to that range (Section 16: counts filterable by date range)."""
    date_range = None
    if date_from or date_to:
        date_range = (local_day_start(date_from or date(2000, 1, 1)),
                      local_day_start((date_to or utcnow().astimezone(PH_TZ).date())
                                      + timedelta(days=1)))
    def queue(status_: RequestStatus) -> list[dict]:
        found = db.scalars(select(DocumentRequest).where(DocumentRequest.status == status_)
                           .order_by(DocumentRequest.id).limit(10))
        return [{"id": r.id, "reference_no": r.reference_no, "document_type": r.document_type,
                 "student_name": f"{r.student.last_name}, {r.student.first_name}",
                 "requested_at": r.requested_at} for r in found]

    blocked = db.scalar(select(func.count()).select_from(DocumentRequest).where(
        DocumentRequest.status == R.APPROVED, DocumentRequest.processing_note.is_not(None)))

    months = _months(6)
    per_month = {m: {"month": m, "TOR": 0, "COE": 0} for m in months}
    for doc_type, created in db.execute(
            select(DocumentRequest.document_type, GeneratedDocument.created_at)
            .join(GeneratedDocument.request)):
        key = as_utc(created).strftime("%Y-%m")
        if key in per_month:
            per_month[key][doc_type.value] = per_month[key].get(doc_type.value, 0) + 1

    counts = select(ValidationRecord.result, func.count()).group_by(ValidationRecord.result)
    if date_range:
        counts = counts.join(ScannedDocument,
                             ScannedDocument.id == ValidationRecord.scanned_document_id).where(
            ScannedDocument.created_at >= date_range[0],
            ScannedDocument.created_at < date_range[1])
    results = Counter(dict(db.execute(counts).all()))
    open_reviews = db.execute(
        select(ScannedDocument, ValidationRecord)
        .join(ValidationRecord, ValidationRecord.scanned_document_id == ScannedDocument.id)
        .where(ValidationRecord.result == ValidationResult.NEEDS_REVIEW,
               ValidationRecord.final_result.is_(None))
        .order_by(ScannedDocument.id)).all()

    activity = db.execute(select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
                          .order_by(AuditLog.id.desc()).limit(20)).all()

    return {
        "requests": _request_cards(db, date_range),
        # S-11: requests waiting for approval, then generated documents waiting for checks.
        "approval_queue": queue(R.REQUESTED),
        "verification_queue": queue(R.FOR_VERIFICATION),
        "blocked_requests": blocked,
        "documents_by_month": list(per_month.values()),
        "validation": {"total": sum(results.values()),
                       **{r.value: results.get(r, 0) for r in ValidationResult}},
        "review_queue": [{"scanned_document_id": s.id,
                          "document_number": scanned_number(s),
                          "reason": v.reasons[0] if v.reasons else None,
                          "scanned_at": s.created_at} for s, v in open_reviews[:10]],
        "open_reviews": len(open_reviews),
        "data_health": _data_health(db),
        "recent_activity": [{"timestamp": log.timestamp, "action": log.action,
                             "user": email, "entity_type": log.entity_type,
                             "entity_id": log.entity_id} for log, email in activity],
    }
