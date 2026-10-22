"""Import every model so Base.metadata is complete for Alembic and tests."""

from app.models.academics import (
    ClassSection,
    Enrollment,
    Grade,
    GradeChangeRequest,
    Program,
    SectionAdviser,
    Subject,
    Term,
)
from app.models.audit import AuditLog
from app.models.documents import DocumentRequest, DocumentTemplate, GeneratedDocument
from app.models.intake import IntakeBatch, IntakeDocument, IntakeSettings
from app.models.notification import NotificationRead
from app.models.people import Student, Teacher, User, UserSession
from app.models.validation import (
    ScannedDocument,
    ScanSession,
    ScanStation,
    StationEventReceipt,
    ValidationRecord,
)

__all__ = [
    "AuditLog",
    "ClassSection",
    "DocumentRequest",
    "DocumentTemplate",
    "Enrollment",
    "GeneratedDocument",
    "Grade",
    "GradeChangeRequest",
    "IntakeBatch",
    "IntakeDocument",
    "IntakeSettings",
    "NotificationRead",
    "Program",
    "ScanSession",
    "ScanStation",
    "ScannedDocument",
    "StationEventReceipt",
    "SectionAdviser",
    "Student",
    "Subject",
    "Teacher",
    "Term",
    "User",
    "UserSession",
    "ValidationRecord",
]
