from enum import StrEnum


class Role(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"
    REGISTRAR = "registrar"
    ADMIN = "admin"
    # Not yet a student: sends documents from another school online (docs/applicant-intake.md).
    # The login becomes a student login when staff link a submission to a student.
    APPLICANT = "applicant"


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
    # From another school, uploaded online by an applicant (docs/applicant-intake.md).
    TRANSCRIPT_OF_RECORDS = "transcript_of_records"
    CERTIFICATE_OF_ENROLLMENT = "certificate_of_enrollment"
    UNKNOWN = "unknown"


class IntakeSource(StrEnum):
    STATION = "station"  # paper fed through the intake station
    ONLINE = "online"  # uploaded by an applicant, read by the OCR worker


class ApplicantSubmissionStatus(StrEnum):
    """draft -> queued -> processing -> screened (an intake document exists), or failed.

    A failed submission (worker gave up after the retries) can be queued again by staff.
    """

    DRAFT = "draft"  # the applicant is still adding pages
    QUEUED = "queued"  # waiting for the OCR worker
    PROCESSING = "processing"  # a worker holds the lease
    SCREENED = "screened"  # read; the intake document is in the staff review queue
    FAILED = "failed"
    WITHDRAWN = "withdrawn"  # by the applicant, before it was read


class ReferenceLabel(StrEnum):
    """Dataset label of a reference sample (docs/applicant-intake.md, "References").

    These are labels of the school's own sample set, never a verdict about an upload: a match
    with a reference sample is not proof of authenticity.
    """

    KNOWN_GOOD_SAMPLE = "known_good_sample"  # a known-good sample from the issuing school
    ALTERED_SAMPLE = "altered_sample"  # a known-altered sample (trains the layout model)
    OTHER = "other"  # not a TOR/COE: trains the classifier's "unknown" class


class ReferenceUse(StrEnum):
    TRAINING = "training"  # reference layout, classifier corpus, layout model
    HELD_OUT = "held_out"  # evaluation only: never trains anything


class ReferenceStatus(StrEnum):
    """queued -> processing -> pending (read, waiting for approval) -> approved -> retired.

    Approved versions can't be edited: upload a new version and retire the old one.
    """

    QUEUED = "queued"  # waiting for the OCR worker to read it
    PROCESSING = "processing"  # a worker holds the lease
    PENDING = "pending"  # read; waiting for staff approval
    APPROVED = "approved"
    RETIRED = "retired"
    FAILED = "failed"  # the worker could not read it (staff may queue it again)


class SecondPassStatus(StrEnum):
    """The second OCR pass, queued when staff accept an online document."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


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


# ---- camera station mechanism, cloud relay (docs/cloud-motor-control.md) -------------------

class MechanismAction(StrEnum):
    """A command the station relay forwards to the Pi's loopback bridge (its action name)."""

    SCAN = "scan"  # feed one sheet; the backend decides VALID / INVALID, never the station
    STOP = "stop"
    ACKNOWLEDGE = "acknowledge"  # operator says the paper path is clear
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    UP = "up"  # sorting servo to an angle (tray setup)
    TEST_MOTOR = "test-motor"
    TEST_SERVO1 = "test-servo1"
    TEST_SERVO2 = "test-servo2"
    TEST_SERVO3 = "test-servo3"
