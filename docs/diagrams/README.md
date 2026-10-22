# Diagrams

UML diagrams of DocuSense (the Academic Document Generation & Validation System): use cases in
PlantUML, the data model as Mermaid class diagrams generated from the code. Every source has an
SVG and a PNG export next to it.

| Diagram | Source | Exports |
| --- | --- | --- |
| Use case overview | [`use-cases.puml`](use-cases.puml) | [SVG](use-cases.svg), [PNG](use-cases.png) |
| Accounts & access | [`use-cases-accounts.puml`](use-cases-accounts.puml) | [SVG](use-cases-accounts.svg), [PNG](use-cases-accounts.png) |
| Academic records | [`use-cases-records.puml`](use-cases-records.puml) | [SVG](use-cases-records.svg), [PNG](use-cases-records.png) |
| Outgoing document requests | [`use-cases-requests.puml`](use-cases-requests.puml) | [SVG](use-cases-requests.svg), [PNG](use-cases-requests.png) |
| Validation of printed documents | [`use-cases-validation.puml`](use-cases-validation.puml) | [SVG](use-cases-validation.svg), [PNG](use-cases-validation.png) |
| Incoming-document screening: station and operator | [`use-cases-intake.puml`](use-cases-intake.puml) | [SVG](use-cases-intake.svg), [PNG](use-cases-intake.png) |
| Incoming-document screening: staff review | [`use-cases-intake-review.puml`](use-cases-intake-review.puml) | [SVG](use-cases-intake-review.svg), [PNG](use-cases-intake-review.png) |
| Administration & audit | [`use-cases-admin.puml`](use-cases-admin.puml) | [SVG](use-cases-admin.svg), [PNG](use-cases-admin.png) |
| Class diagrams (overview + 6 areas) | `classes-*.mmd`, generated | `classes-*.svg`, `classes-*.png` |

## Regenerating

```bash
# 1. Class diagrams: after any change to backend/app/models/ (also refreshes this README)
cd backend && .venv/Scripts/python scripts/gen_class_diagram.py

# 2. SVG + PNG exports of every .puml and .mmd (needs internet; standard library only)
python docs/diagrams/render.py               # all diagrams
python docs/diagrams/render.py use-cases     # only sources whose name contains "use-cases"
```

`render.py` posts each source to [Kroki](https://kroki.io) (`/plantuml/svg|png`,
`/mermaid/svg|png`); for PlantUML it falls back to the public PlantUML server. It checks every
result (the SVG parses as XML, the PNG is complete) before replacing a file and exits 1 if any
export failed. The sources contain no personal data or secrets.

Two backend tests keep the diagrams honest (both run with the normal `pytest`):

- `tests/test_class_diagram.py` regenerates the class diagrams into a temporary folder and fails
  if the committed `classes-*.mmd` files or the generated block below are stale.
- `tests/test_use_case_diagram.py` reads the `' @routes` tags in the use case sources and checks
  every actor link against `tests/test_access_matrix.py` (who may call which route), that every
  route is drawn for the roles that can use it, and that the overview matches the details.
  When you change who may call a route, update the matrix and the diagram together.

Exports are not checked for staleness: re-run `render.py` after editing a source.

## Use cases (PlantUML)

The overview shows which actor works with which part of the system. Each package then has its
own diagram with the individual use cases.

- **Actors.** Student, Teacher, Registrar and Administrator are the four login roles; all of
  them are signed-in users (log in, change password, browse programs, subjects and terms). A
  *class adviser* is a teacher assigned to a block section; a *validation operator* is a
  registrar whose account has the validation permission. Both inherit their parent's use cases.
  The *scan station* (validation of printed documents) and the *intake station* (camera, feeder
  and two trays for incoming documents) are system actors that call the API with a station key.
- **Outgoing documents** follow the approve-before-generate order: request, registrar approves
  (records are retrieved and the document generated only then), registrar checks the generated
  document (verify), release, student download.
- **Incoming documents** are screened: the station measures, the backend decides *passed
  screening* (Tray 1) or *needs manual review* (Tray 2), and staff work the review queue.
- **Not drawn as use cases:** the health check, and the daily job the scheduler calls with its
  own secret (`GET /api/internal/cron/daily`: expires download windows, purges idle sessions).

![Use case overview](use-cases.svg)

| | |
| --- | --- |
| ![Accounts & access](use-cases-accounts.svg) | ![Academic records](use-cases-records.svg) |
| ![Outgoing document requests](use-cases-requests.svg) | ![Validation of printed documents](use-cases-validation.svg) |
| ![Screening: station and operator](use-cases-intake.svg) | ![Screening: staff review](use-cases-intake-review.svg) |
| ![Administration & audit](use-cases-admin.svg) | |

## Class diagrams (Mermaid, generated from the models)

`backend/scripts/gen_class_diagram.py` imports `backend/app/models` and walks
`Base.metadata` and the mapper registry. The overview shows every table and its foreign-key
relationships without attributes; each area diagram shows primary and foreign keys, unique
columns, enum and flag columns and the key business fields, with the enumerations
(`<<enumeration>>`) they use. Classes from other areas are drawn grey and dashed.

Multiplicities come from the foreign keys: the referenced side is `1` (NOT NULL) or `0..1`
(nullable); the referencing side is `0..1` for a unique column, else `*`. Columns that only
record which staff member did something (`created_by`, `reviewed_by`, ...) are left out of the
overview and shown in the area diagrams. Field meanings: [data dictionary](../data-dictionary.md).

<!-- BEGIN generated class diagrams (backend/scripts/gen_class_diagram.py) -->

### Overview (relationships only)

Source [`classes-overview.mmd`](classes-overview.mmd) · exports [SVG](classes-overview.svg), [PNG](classes-overview.png)

```mermaid
%% Class diagram overview: every table, relationships only
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction TB
    %% people: People and accounts
    class Student["Student (students)"]
    class Teacher["Teacher (teachers)"]
    class UserSession["UserSession (user_sessions)"]
    class User["User (users)"]
    %% academics: Academics: programs, subjects, terms, sections, advisers, grades
    class ClassSection["ClassSection (class_sections)"]
    class Enrollment["Enrollment (enrollments)"]
    class GradeChangeRequest["GradeChangeRequest (grade_change_requests)"]
    class Grade["Grade (grades)"]
    class Program["Program (programs)"]
    class SectionAdviser["SectionAdviser (section_advisers)"]
    class Subject["Subject (subjects)"]
    class Term["Term (terms)"]
    %% documents: Requests, generated documents and templates
    class DocumentRequest["DocumentRequest (document_requests)"]
    class DocumentTemplate["DocumentTemplate (document_templates)"]
    class GeneratedDocument["GeneratedDocument (generated_documents)"]
    %% validation: Validation of printed documents and scan stations
    class ScanSession["ScanSession (scan_sessions)"]
    class ScanStation["ScanStation (scan_stations)"]
    class ScannedDocument["ScannedDocument (scanned_documents)"]
    class ValidationRecord["ValidationRecord (validation_records)"]
    %% intake: Incoming-document screening
    class IntakeBatch["IntakeBatch (intake_batches)"]
    class IntakeDocument["IntakeDocument (intake_documents)"]
    class IntakeSettings["IntakeSettings (intake_settings)"]
    %% audit: Audit trail
    class AuditLog["AuditLog (audit_logs)"]
    AuditLog "*" --> "0..1" User : user_id
    ClassSection "*" --> "1" Subject : subject_id
    ClassSection "*" --> "0..1" Teacher : teacher_id
    ClassSection "*" --> "1" Term : term_id
    DocumentRequest "*" --> "1" Student : student_id
    Enrollment "*" --> "1" ClassSection : class_section_id
    Enrollment "*" --> "1" Student : student_id
    GeneratedDocument "*" --> "1" DocumentRequest : request_id
    GeneratedDocument "*" --> "1" DocumentTemplate : template_id
    Grade "0..1" --> "1" Enrollment : enrollment_id
    GradeChangeRequest "*" --> "1" Grade : grade_id
    IntakeBatch "*" --> "1" ScanStation : station_id
    IntakeDocument "*" --> "0..1" IntakeBatch : batch_id
    IntakeDocument "*" --> "1" ScanStation : station_id
    IntakeDocument "*" --> "0..1" Student : student_id
    ScanSession "*" --> "0..1" ScanStation : station_id
    ScannedDocument "*" --> "0..1" GeneratedDocument : matched_document_id
    ScannedDocument "*" --> "1" ScanSession : session_id
    SectionAdviser "*" --> "1" Teacher : teacher_id
    SectionAdviser "*" --> "1" Term : term_id
    Student "*" --> "0..1" Program : program_id
    Student "0..1" --> "0..1" User : user_id
    Subject "*" --> "0..1" Program : program_id
    Teacher "0..1" --> "0..1" User : user_id
    UserSession "*" --> "1" User : user_id
    ValidationRecord "0..1" --> "1" ScannedDocument : scanned_document_id
    note for User "Also referenced by 15 staff columns (created_by, reviewed_by, ...),<br>shown in the area diagrams"
```

### People and accounts

Source [`classes-people.mmd`](classes-people.mmd) · exports [SVG](classes-people.svg), [PNG](classes-people.png)

```mermaid
%% Class diagram: People and accounts
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction LR
    class Student["Student (students)"] {
        int id PK
        int program_id FK
        int user_id FK,UK
        str first_name
        str last_name
        str lrn UK
        StudentStatus status
        str student_number UK
        int year_level
    }
    class Teacher["Teacher (teachers)"] {
        int id PK
        int user_id FK,UK
        str employee_number UK
        str full_name
    }
    class User["User (users)"] {
        int id PK
        bool can_validate
        str email UK
        bool is_active
        bool is_locked
        datetime last_login_at
        bool must_change_password
        Role role
    }
    class UserSession["UserSession (user_sessions)"] {
        int id PK
        int user_id FK
        datetime last_seen_at
        str token_hash UK
    }
    class Role {
        <<enumeration>>
        STUDENT = student
        TEACHER = teacher
        REGISTRAR = registrar
        ADMIN = admin
    }
    class StudentStatus {
        <<enumeration>>
        ACTIVE = active
        GRADUATED = graduated
        INACTIVE = inactive
    }
    class Program
    Student "*" --> "0..1" Program : program_id
    Student "0..1" --> "0..1" User : user_id
    Teacher "0..1" --> "0..1" User : user_id
    UserSession "*" --> "1" User : user_id
    Student ..> StudentStatus
    User ..> Role
    style Program fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
```

### Academics: programs, subjects, terms, sections, advisers, grades

Source [`classes-academics.mmd`](classes-academics.mmd) · exports [SVG](classes-academics.svg), [PNG](classes-academics.png)

```mermaid
%% Class diagram: Academics: programs, subjects, terms, sections, advisers, grades
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction TB
    class ClassSection["ClassSection (class_sections)"] {
        int id PK
        int subject_id FK
        int teacher_id FK
        int term_id FK
        datetime grades_submitted_at
        str section_code
    }
    class Enrollment["Enrollment (enrollments)"] {
        int id PK
        int class_section_id FK
        int student_id FK
        EnrollmentStatus status
    }
    class Grade["Grade (grades)"] {
        int id PK
        int encoded_by FK
        int enrollment_id FK,UK
        decimal grade_value
        bool is_locked
        GradeRemark remarks
    }
    class GradeChangeRequest["GradeChangeRequest (grade_change_requests)"] {
        int id PK
        int approved_by FK
        int grade_id FK
        int requested_by FK
        str new_value
        str old_value
        text reason
        ChangeRequestStatus status
    }
    class Program["Program (programs)"] {
        int id PK
        str code UK
        str name
    }
    class SectionAdviser["SectionAdviser (section_advisers)"] {
        int id PK
        int assigned_by FK
        int teacher_id FK
        int term_id FK
        str section_code
    }
    class Subject["Subject (subjects)"] {
        int id PK
        int program_id FK
        str code UK
        str title
        decimal units
    }
    class Term["Term (terms)"] {
        int id PK
        str academic_year
        bool is_current
        bool is_grades_locked
        Semester semester
    }
    class ChangeRequestStatus {
        <<enumeration>>
        PENDING = pending
        APPROVED = approved
        REJECTED = rejected
    }
    class EnrollmentStatus {
        <<enumeration>>
        ENROLLED = enrolled
        DROPPED = dropped
    }
    class GradeRemark {
        <<enumeration>>
        PASSED = Passed
        FAILED = Failed
        INC = INC
        DRP = DRP
    }
    class Semester {
        <<enumeration>>
        FIRST = 1st
        SECOND = 2nd
        SUMMER = Summer
    }
    class Student
    class Teacher
    class User
    ClassSection "*" --> "1" Subject : subject_id
    ClassSection "*" --> "0..1" Teacher : teacher_id
    ClassSection "*" --> "1" Term : term_id
    Enrollment "*" --> "1" ClassSection : class_section_id
    Enrollment "*" --> "1" Student : student_id
    Grade "*" --> "0..1" User : encoded_by
    Grade "0..1" --> "1" Enrollment : enrollment_id
    GradeChangeRequest "*" --> "0..1" User : approved_by
    GradeChangeRequest "*" --> "1" Grade : grade_id
    GradeChangeRequest "*" --> "1" User : requested_by
    SectionAdviser "*" --> "0..1" User : assigned_by
    SectionAdviser "*" --> "1" Teacher : teacher_id
    SectionAdviser "*" --> "1" Term : term_id
    Subject "*" --> "0..1" Program : program_id
    Enrollment ..> EnrollmentStatus
    Grade ..> GradeRemark
    GradeChangeRequest ..> ChangeRequestStatus
    Term ..> Semester
    style Student fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    style Teacher fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    style User fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
```

### Requests, generated documents and templates

Source [`classes-documents.mmd`](classes-documents.mmd) · exports [SVG](classes-documents.svg), [PNG](classes-documents.png)

```mermaid
%% Class diagram: Requests, generated documents and templates
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction LR
    class DocumentRequest["DocumentRequest (document_requests)"] {
        int id PK
        int requested_by FK
        int reviewed_by FK
        int student_id FK
        int copies
        DocumentType document_type
        bool payment_confirmed
        text processing_note
        str purpose
        str reference_no UK
        datetime requested_at
        RequestStatus status
    }
    class DocumentTemplate["DocumentTemplate (document_templates)"] {
        int id PK
        int created_by FK
        DocumentType document_type
        json field_zones
        TemplateStatus status
        int version
    }
    class GeneratedDocument["GeneratedDocument (generated_documents)"] {
        int id PK
        int approved_by FK
        int request_id FK
        int template_id FK
        str data_hash
        json data_snapshot
        str document_number UK
        datetime download_expires_at
        int page_count
        str qr_token UK
        datetime released_at
        GeneratedDocumentStatus status
    }
    class DocumentType {
        <<enumeration>>
        TOR = TOR
        COE = COE
    }
    class GeneratedDocumentStatus {
        <<enumeration>>
        GENERATED = generated
        APPROVED = approved
        RELEASED = released
        REVOKED = revoked
    }
    class RequestStatus {
        <<enumeration>>
        REQUESTED = requested
        APPROVED = approved
        FOR_VERIFICATION = for_verification
        VERIFIED = verified
        RELEASED = released
        REJECTED = rejected
        CANCELLED = cancelled
        EXPIRED = expired
        REVOKED = revoked
    }
    class TemplateStatus {
        <<enumeration>>
        DRAFT = draft
        ACTIVE = active
        RETIRED = retired
    }
    class Student
    class User
    DocumentRequest "*" --> "0..1" User : requested_by
    DocumentRequest "*" --> "0..1" User : reviewed_by
    DocumentRequest "*" --> "1" Student : student_id
    DocumentTemplate "*" --> "0..1" User : created_by
    GeneratedDocument "*" --> "0..1" User : approved_by
    GeneratedDocument "*" --> "1" DocumentRequest : request_id
    GeneratedDocument "*" --> "1" DocumentTemplate : template_id
    DocumentRequest ..> DocumentType
    DocumentRequest ..> RequestStatus
    DocumentTemplate ..> DocumentType
    DocumentTemplate ..> TemplateStatus
    GeneratedDocument ..> GeneratedDocumentStatus
    style Student fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    style User fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
```

### Validation of printed documents and scan stations

Source [`classes-validation.mmd`](classes-validation.mmd) · exports [SVG](classes-validation.svg), [PNG](classes-validation.png)

```mermaid
%% Class diagram: Validation of printed documents and scan stations
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction TB
    class ScanSession["ScanSession (scan_sessions)"] {
        int id PK
        int operator_id FK
        int station_id FK
        datetime ended_at
        ScanMode mode
        datetime started_at
        int total_docs
    }
    class ScanStation["ScanStation (scan_stations)"] {
        int id PK
        int registered_by FK
        str api_key_hash UK
        bool is_active
        datetime last_seen_at
        str last_status
        str name UK
    }
    class ScannedDocument["ScannedDocument (scanned_documents)"] {
        int id PK
        int matched_document_id FK
        int session_id FK
        float ocr_confidence
        int position_in_stack
    }
    class ValidationRecord["ValidationRecord (validation_records)"] {
        int id PK
        int reviewed_by FK
        int scanned_document_id FK,UK
        ValidationResult final_result
        json reasons
        ValidationResult result
    }
    class ScanMode {
        <<enumeration>>
        SINGLE = single
        BULK = bulk
    }
    class ValidationResult {
        <<enumeration>>
        VALID = VALID
        INVALID = INVALID
        NEEDS_REVIEW = NEEDS_REVIEW
    }
    class GeneratedDocument
    class User
    ScanSession "*" --> "1" User : operator_id
    ScanSession "*" --> "0..1" ScanStation : station_id
    ScanStation "*" --> "0..1" User : registered_by
    ScannedDocument "*" --> "0..1" GeneratedDocument : matched_document_id
    ScannedDocument "*" --> "1" ScanSession : session_id
    ValidationRecord "*" --> "0..1" User : reviewed_by
    ValidationRecord "0..1" --> "1" ScannedDocument : scanned_document_id
    ScanSession ..> ScanMode
    ValidationRecord ..> ValidationResult
    style GeneratedDocument fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    style User fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
```

### Incoming-document screening

Source [`classes-intake.mmd`](classes-intake.mmd) · exports [SVG](classes-intake.svg), [PNG](classes-intake.png)

```mermaid
%% Class diagram: Incoming-document screening
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction LR
    class IntakeBatch["IntakeBatch (intake_batches)"] {
        int id PK
        int station_id FK
        datetime ended_at
        int manual_review_count
        int passed_count
        datetime started_at
        int total_docs
    }
    class IntakeDocument["IntakeDocument (intake_documents)"] {
        int id PK
        int batch_id FK
        int fields_reviewed_by FK
        int linked_by FK
        int reviewed_by FK
        int station_id FK
        int student_id FK
        int assigned_tray
        IntakeDecision decision
        IntakeDocType doc_type
        float doc_type_confidence
        json reasons
        json reviewed_fields
        int routed_tray
        bool routing_jam
        json screening_result
        int sequence
        IntakeStatus status
    }
    class IntakeSettings["IntakeSettings (intake_settings)"] {
        int id PK
        int updated_by FK
        float max_indicator_severity
        float min_doc_type_confidence
        float min_layout_score
        float min_similarity_score
    }
    class IntakeDecision {
        <<enumeration>>
        PASSED_SCREENING = passed_screening
        MANUAL_REVIEW = manual_review
    }
    class IntakeDocType {
        <<enumeration>>
        PSA_BIRTH_CERTIFICATE = psa_birth_certificate
        JHS_REPORT_CARD = jhs_report_card
        GOOD_MORAL_CERTIFICATE = good_moral_certificate
        JHS_COMPLETION_CERTIFICATE = jhs_completion_certificate
        UNKNOWN = unknown
    }
    class IntakeStatus {
        <<enumeration>>
        SCREENED = screened
        ACCEPTED = accepted
        REJECTED = rejected
        RESUBMISSION_REQUESTED = resubmission_requested
        DATA_REVIEWED = data_reviewed
        LINKED = linked
    }
    class ScanStation
    class Student
    class User
    IntakeBatch "*" --> "1" ScanStation : station_id
    IntakeDocument "*" --> "0..1" IntakeBatch : batch_id
    IntakeDocument "*" --> "0..1" User : fields_reviewed_by
    IntakeDocument "*" --> "0..1" User : linked_by
    IntakeDocument "*" --> "0..1" User : reviewed_by
    IntakeDocument "*" --> "1" ScanStation : station_id
    IntakeDocument "*" --> "0..1" Student : student_id
    IntakeSettings "*" --> "0..1" User : updated_by
    IntakeDocument ..> IntakeDecision
    IntakeDocument ..> IntakeDocType
    IntakeDocument ..> IntakeStatus
    style ScanStation fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    style Student fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    style User fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
```

### Audit trail

Source [`classes-audit.mmd`](classes-audit.mmd) · exports [SVG](classes-audit.svg), [PNG](classes-audit.png)

```mermaid
%% Class diagram: Audit trail
%% Generated by backend/scripts/gen_class_diagram.py from backend/app/models.
%% Do not edit by hand: re-run the script (tests/test_class_diagram.py checks it).
classDiagram
    direction LR
    class AuditLog["AuditLog (audit_logs)"] {
        int id PK
        int user_id FK
        str action
        str entity_id
        str entity_type
        datetime timestamp
    }
    class User
    AuditLog "*" --> "0..1" User : user_id
    style User fill:#eeeeee,stroke:#888888,stroke-dasharray:4 4
    note for AuditLog "entity_type + entity_id point at any row (no foreign key).<br>Append-only: the API login may only INSERT and SELECT."
```

<!-- END generated class diagrams -->
