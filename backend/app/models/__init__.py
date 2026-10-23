"""Import every model so Base.metadata is complete for Alembic and tests."""

from app.models.academics import (
    ClassSection,
    Enrollment,
    Grade,
    GradeChangeRequest,
    Program,
    SectionAdviser,
    StudentAdviser,
    Subject,
    Term,
)
from app.models.applicant import (
    ApplicantPage,
    ApplicantReference,
    ApplicantSecondPass,
    ApplicantSubmission,
)
from app.models.audit import AuditLog
from app.models.documents import DocumentRequest, DocumentTemplate, GeneratedDocument
from app.models.intake import IntakeBatch, IntakeDocument, IntakeSettings
from app.models.mechanism import MechanismCommand, MechanismStationState
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
    "ApplicantPage",
    "ApplicantReference",
    "ApplicantSecondPass",
    "ApplicantSubmission",
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
    "MechanismCommand",
    "MechanismStationState",
    "NotificationRead",
    "Program",
    "ScanSession",
    "ScanStation",
    "ScannedDocument",
    "StationEventReceipt",
    "SectionAdviser",
    "Student",
    "StudentAdviser",
    "Subject",
    "Teacher",
    "Term",
    "User",
    "UserSession",
    "ValidationRecord",
]
