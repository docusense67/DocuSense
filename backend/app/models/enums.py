from enum import StrEnum


class Role(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"
    REGISTRAR = "registrar"
    ADMIN = "admin"


class StudentStatus(StrEnum):
    ACTIVE = "active"
    GRADUATED = "graduated"
    INACTIVE = "inactive"


class Semester(StrEnum):
    FIRST = "1st"
    SECOND = "2nd"
    SUMMER = "Summer"


class EnrollmentStatus(StrEnum):
    ENROLLED = "enrolled"
    DROPPED = "dropped"


class GradeRemark(StrEnum):
    PASSED = "Passed"
    FAILED = "Failed"
    INC = "INC"
    DRP = "DRP"


class ChangeRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class DocumentType(StrEnum):
    TOR = "TOR"
    COE = "COE"


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class RequestStatus(StrEnum):
    """Section 14 request lifecycle, approve-first order (change S-11).

    The registrar approves the request before records are retrieved and the document is
    generated, then verifies the generated document before it is released. Stored by NAME
    (native_enum=False); migration e7b2c4d9a1f3 mapped the old generate-first states.
    """

    REQUESTED = "requested"  # waiting for the registrar's approval (student: Pending)
    APPROVED = "approved"  # request approved; generating, or blocked (processing_note)
    FOR_VERIFICATION = "for_verification"  # document generated; registrar verifies it
    VERIFIED = "verified"  # document verified; waiting for a manual release (pickup)
    RELEASED = "released"  # downloadable and verifiable (student: Completed)
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ClientStatus(StrEnum):
    """What students see (Updated Features II-B): Pending / Approved / Rejected / Completed,
    plus the end states that exist in the lifecycle."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REVOKED = "revoked"


class GeneratedDocumentStatus(StrEnum):
    GENERATED = "generated"
    APPROVED = "approved"
    RELEASED = "released"
    REVOKED = "revoked"


class ScanMode(StrEnum):
    SINGLE = "single"
    BULK = "bulk"


class ValidationResult(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# ---- incoming-document screening (Part I, docs/intake/screening-contract.md) ----------------

class IntakeDocType(StrEnum):
    PSA_BIRTH_CERTIFICATE = "psa_birth_certificate"
    JHS_REPORT_CARD = "jhs_report_card"
    GOOD_MORAL_CERTIFICATE = "good_moral_certificate"
    JHS_COMPLETION_CERTIFICATE = "jhs_completion_certificate"
    UNKNOWN = "unknown"


class IntakeDecision(StrEnum):
    """Screening outcome. Never 'authentic'/'genuine'/'verified' (contract wording rule)."""

    PASSED_SCREENING = "passed_screening"  # Tray 1
    MANUAL_REVIEW = "manual_review"  # Tray 2


class IntakeStatus(StrEnum):
    """Processing status. screened -> accepted -> data_reviewed -> linked (S-06/S-07).

    A document that passed screening may go straight from screened to data_reviewed: saving its
    reviewed fields accepts it (audited as an acceptance). Rejected and resubmission-requested
    documents never reach data_reviewed or linked.
    """

    SCREENED = "screened"  # waiting for a staff decision
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RESUBMISSION_REQUESTED = "resubmission_requested"
    DATA_REVIEWED = "data_reviewed"  # staff reviewed/corrected the extracted fields
    LINKED = "linked"  # associated with a student profile (existing or created from it)


class IntakeReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUEST_RESUBMISSION = "request_resubmission"
