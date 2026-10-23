export interface MechanismStatus {
  enabled: boolean
  station_id?: number
  connected?: boolean
  busy?: boolean
  state?: string
  label?: string
  remaining?: number
  camera_focus?: { supported: boolean | null; state: string; message: string }
  error?: string | null
  workflow?: {
    active: boolean
    held: boolean
    stage: string
    progress?: number
    error?: string | null
    message: string
    session_id: number | null
    result: { result: Result; scanned_document_id: number } | null
  } | null
}
export interface MechanismCommand {
  action: 'scan' | 'stop' | 'heartbeat' | 'acknowledge' | 'tray' | 'connect' | 'disconnect'
    | 'test-motor' | 'test-servo1' | 'test-servo2' | 'test-servo3'
  session_id?: number
  angle?: number
}

/** applicant: not yet a student, sends documents from another school (docs/applicant-intake.md). */
export type Role = 'student' | 'teacher' | 'registrar' | 'admin' | 'applicant'
export type DocType = 'TOR' | 'COE'
export type Result = 'VALID' | 'INVALID' | 'NEEDS_REVIEW'
/** Registrar sub-steps, approve-first order (S-11): approve the request, then generate, verify, release. */
export type RequestStatus =
  | 'requested' | 'approved' | 'for_verification' | 'verified' | 'released'
  | 'rejected' | 'cancelled' | 'expired' | 'revoked'
/** What students see: Pending / Approved / Rejected / Completed (plus the end states). */
export type ClientStatus = 'pending' | 'approved' | 'rejected' | 'completed' | 'cancelled' | 'expired' | 'revoked'

export interface User {
  id: number
  email: string
  role: Role
  is_active: boolean
  is_locked: boolean
  can_validate: boolean
  must_change_password: boolean
  last_login_at: string | null
}

export interface DocSummary {
  id: number
  document_number: string
  status: 'generated' | 'approved' | 'released' | 'revoked'
  page_count: number
  released_at: string | null
  download_expires_at: string | null
  can_download: boolean
  downloads: string[]
}

export interface DocRequest {
  id: number
  reference_no: string
  student_id: number
  student_number: string
  student_name: string
  document_type: DocType
  purpose: string
  copies: number
  remarks: string | null
  status: RequestStatus
  client_status: ClientStatus
  payment_confirmed: boolean
  reject_reason: string | null
  processing_note: string | null
  requested_at: string
  document: DocSummary | null
}

export interface DocRequestDetail extends DocRequest {
  snapshot: Snapshot | null
  current_data: Snapshot | null
  current_data_error: string[] | null
  differs_from_records: boolean | null
  payment_required: boolean
  auto_release: boolean
}

export interface Notification {
  id: number
  title: string
  message: string
  timestamp: string
  href: string
  read: boolean
}

export interface TimelineEntry {
  timestamp: string
  action: string
  user_email: string | null
  entity_type: string | null
  details: Record<string, unknown> | null
}

export interface AuditEntry {
  id: number
  timestamp: string
  action: string
  user_email: string | null
  entity_type: string | null
  entity_id: string | null
  old_value: Record<string, unknown> | null
  new_value: Record<string, unknown> | null
  ip_address: string | null
}

export interface ChangeRequest {
  id: number
  grade_id: number
  old_value: string | null
  new_value: string
  reason: string
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  student_number: string
  student_name: string
  subject_code: string
  section_code: string
  term: string
  requested_by_email: string | null
}

/** Per-row problems from an all-or-nothing CSV/Excel import (spreadsheet row numbers). */
export interface ImportErrors { detail: string; errors: { row: number; errors: string[] }[] }

export interface ScanFilters { result?: Result | ''; document_type?: DocType | ''; date_from?: string; date_to?: string }

export interface Subject {
  subject_code: string
  subject_title: string
  units: string
  grade: string
  remarks: string
}
export interface Term { semester: string; academic_year: string; gwa: string; subjects: Subject[] }
export type Snapshot = Record<string, string> & { terms?: Term[] }

export interface TermRecord {
  term_id: number
  academic_year: string
  semester: string
  is_grades_locked: boolean
  gwa: string | null
  subjects: { subject_code: string; subject_title: string; units: string; grade: string | null; remarks: string | null }[]
}

export interface StudentRow {
  id: number
  user_id: number | null
  student_number: string
  first_name: string
  middle_name: string | null
  last_name: string
  suffix: string | null
  program_id: number | null
  year_level: number | null
  status: 'active' | 'graduated' | 'inactive'
  login_active: boolean | null
  birthdate?: string | null
  sex?: string | null
  admission_date?: string | null
  lrn?: string | null
  birth_place?: string | null
  mother_name?: string | null
  father_name?: string | null
}

export interface Program { id: number; code: string; name: string; department: string | null; total_units: number | null }
export interface SubjectRow { id: number; code: string; title: string; units: string; program_id: number | null }
export interface Teacher { id: number; employee_number: string; full_name: string; department: string | null; user_id: number | null }
export interface SectionRow extends Section { subject_id: number; term_id: number; teacher_id: number | null }

/** A block section (term + section code) and its class adviser (S-08). */
export interface AdviserRow {
  term_id: number
  academic_year: string
  semester: string
  section_code: string
  subjects: number
  students: number
  teacher_id: number | null
  teacher_name: string | null
}

export interface AdvisoryClass {
  term_id: number
  academic_year: string
  semester: string
  is_grades_locked: boolean
  section_code: string
  sections: { id: number; subject_code: string; subject_title: string; teacher_name: string | null }[]
  students: { student_id: number; student_number: string; student_name: string; program_code: string | null; year_level: number | null }[]
}
export interface TermRow { id: number; academic_year: string; semester: string; is_grades_locked: boolean; is_current: boolean }

export interface Section {
  id: number
  subject_code: string
  subject_title: string
  section_code: string
  academic_year: string
  semester: string
  is_grades_locked: boolean
  grades_submitted: boolean
  /** For teachers: teaches it (may encode) and/or advises its block (view only). */
  teaching: boolean
  advising: boolean
}

export interface GradeRow {
  enrollment_id: number
  student_number: string
  student_name: string
  grade_id: number | null
  grade: string | null
  remarks: string | null
  is_locked: boolean
}

export interface Validation {
  placeholders: string[]
  unknown_placeholders: string[]
  missing_placeholders: string[]
  missing_zones: string[]
  errors: string[]
  can_activate: boolean
}

export interface Template {
  id: number
  document_type: DocType
  version: number
  status: 'draft' | 'active' | 'retired'
  html_content: string
  field_zones: Zones | null
  created_at: string
  validation: Validation
}

export interface Zone { x: number; y: number; w: number; h: number; pages?: string }
export interface Zones { unit: 'mm'; fields: Record<string, Zone> }

export interface ScanRow {
  scanned_document_id: number
  position: number | null
  document_number: string | null
  matched: boolean
  result: Result | null
  final_result: Result | null
  top_reason: string | null
  scanned_at: string
}

export interface FieldResult {
  field: string
  printed: string | null
  official: string | null
  status: 'match' | 'mismatch' | 'similar' | 'uncertain' | 'missing'
  confidence: number | null
  critical: boolean
  zone?: (Zone & { page?: number }) | null
}

export interface ScanDetail extends ScanRow {
  reasons: string[]
  fields: FieldResult[]
  review: { final_result: Result | null; remarks: string | null; reviewed_by: number | null }
  document: { id: number; document_number: string; status: string; document_type: DocType; student_name: string; times_scanned: number } | null
  page_images: number
  ocr_pages?: { page: number; text: string | null; truncated: boolean; error: string | null }[]
  zones: Zones | null
  note: string
}

export interface ScanSession {
  session_id: number
  mode: 'single' | 'bulk'
  started_at: string
  ended_at: string | null
  total: number
  totals: Record<Result, number>
  documents: ScanRow[]
}

export interface Station {
  id: number
  name: string
  status: string | null
  status_detail: string | null
  last_seen_at: string | null
  silent: boolean
}

export interface QueueItem { id: number; reference_no: string; document_type: DocType; student_name: string; requested_at: string }

export interface RegistrarDashboard {
  requests: Partial<Record<'today' | 'week' | 'month' | 'range', { total: number; pending: number; approved: number; released: number; rejected: number }>>
  approval_queue: QueueItem[]
  verification_queue: QueueItem[]
  blocked_requests: number
  documents_by_month: { month: string; TOR: number; COE: number }[]
  validation: Record<Result | 'total', number>
  review_queue: { scanned_document_id: number; document_number: string | null; reason: string | null; scanned_at: string }[]
  open_reviews: number
  data_health: { student_id: number; student_number: string; name: string; issues: string[] }[]
  recent_activity: { timestamp: string; action: string; user: string | null; entity_type: string | null; entity_id: string | null }[]
}

// ---- incoming-document screening (Part I, docs/intake/screening-contract.md) ----
export type IntakeDocType =
  | 'psa_birth_certificate' | 'jhs_report_card' | 'good_moral_certificate'
  | 'jhs_completion_certificate' | 'transcript_of_records' | 'certificate_of_enrollment' | 'unknown'
/** station: paper through the intake station; online: uploaded by an applicant. */
export type IntakeSource = 'station' | 'online'
export type IntakeDecision = 'passed_screening' | 'manual_review'
/** screened -> accepted -> data_reviewed -> linked (S-06/S-07); rejected / resubmission end it. */
export type IntakeStatus = 'screened' | 'accepted' | 'rejected' | 'resubmission_requested' | 'data_reviewed' | 'linked'
export type IntakeAction = 'accept' | 'reject' | 'request_resubmission'

export interface IntakeRow {
  id: number
  source: IntakeSource
  submission_id: number | null
  batch_id: number | null
  sequence: number | null
  doc_type: IntakeDocType
  doc_type_confidence: number | null
  decision: IntakeDecision
  reasons: string[]
  assigned_tray: 1 | 2
  routed_tray: 1 | 2 | null
  routing_jam: boolean
  status: IntakeStatus
  created_at: string
  reviewed_at: string | null
  fields_reviewed_at: string | null
  student_id: number | null
  linked_at: string | null
}

export interface IntakeIndicator { code?: string; severity?: number; detail?: string; box?: number[] }

export interface IntakeDetail extends IntakeRow {
  station: string | null
  /** Online uploads: every page is served by api.applicantPageUrl. */
  submission: {
    id: number; pages: number; declared_type: IntakeDocType; issuing_school: string
    applicant_email: string | null; uploaded_by: string | null; submitted_at: string | null
  } | null
  assessment: IntakeAssessment | null
  second_pass: SecondPassView | null
  predicted_doc_type: string | null
  reasons_explained: { code: string; text: string }[]
  indicators: IntakeIndicator[]
  quality: Record<string, unknown> | null
  checks: Record<string, unknown> | null
  similarity: Record<string, unknown> | null
  fields: Record<string, { value?: unknown; confidence?: unknown }> | null
  has_image: boolean
  student: { id: number; student_number: string; name: string } | null
  review: { reviewed_by: string | null; reviewed_at: string | null; reason: string | null }
  history: TimelineEntry[]
  note: string
}

// ---- S-06/S-07: reviewed fields, student matching, link or create a profile ----
export type IntakeFieldKind = 'name' | 'person' | 'text' | 'date' | 'lrn' | 'sex' | 'school_year' | 'grade_level' | 'average'
export interface IntakeFieldSpec {
  key: string
  label: string
  kind: IntakeFieldKind
  required: boolean
  student_attr: string | null
  hint: string | null
  max_length: number
}
export interface IntakeExtracted { value: string | null; read_as: string | null; confidence: number | null; derived: boolean }
export interface IntakeStudentRef { id: number; student_number: string; name: string; lrn: string | null; birthdate: string | null }
export interface IntakeFields {
  intake_id: number
  status: IntakeStatus
  decision: IntakeDecision
  doc_type: IntakeDocType
  schema_doc_type: IntakeDocType
  doc_types: IntakeDocType[]
  schema: IntakeFieldSpec[]
  extracted: Record<string, IntakeExtracted>
  reviewed: { values: Record<string, string | null>; changed: string[]; reviewed_by: string | null; reviewed_at: string | null } | null
  editable: boolean
  blocked: string | null
  student: IntakeStudentRef | null
  linked_by: string | null
  linked_at: string | null
}
export type MatchRule = 'lrn' | 'name_birthdate' | 'name' | 'name_birthdate_differs' | 'first_name_birthdate'
export interface IntakeCandidate {
  student_id: number
  student_number: string
  name: string
  birthdate: string | null
  lrn: string | null
  match: MatchRule
  strength: 'strong' | 'possible'
  conflicts: ('lrn_differs' | 'name_differs' | 'birthdate_differs')[]
}

export interface IntakeFilters { status?: IntakeStatus | ''; doc_type?: IntakeDocType | ''; decision?: IntakeDecision | ''; batch_id?: string; student_id?: number; source?: IntakeSource | '' }

// ---- applicant intake (docs/applicant-intake.md) ----
export type ApplicantDocType = 'transcript_of_records' | 'certificate_of_enrollment'
export type ApplicantSubmissionStatus = 'draft' | 'queued' | 'processing' | 'screened' | 'failed' | 'withdrawn'
/** The applicant's own view: plain status text, no staff notes. */
export interface ApplicantSubmission {
  id: number
  declared_type: ApplicantDocType
  issuing_school: string
  status: ApplicantSubmissionStatus
  status_text: string
  pages: number
  created_at: string
  submitted_at: string | null
}
export type SecondPassStatus = 'queued' | 'processing' | 'done' | 'failed'
export interface StaffApplicantSubmission extends ApplicantSubmission {
  applicant_id: number
  applicant_email: string | null
  /** Set when registrar staff created it on the applicant's behalf. */
  uploaded_by: string | null
  attempts: number
  error: string | null
  intake_document_id: number | null
  lease_expires_at: string | null
  second_pass_status: SecondPassStatus | null
  second_pass_error: string | null
  second_pass_disagreements: number
}
export type ReportFormat = 'json' | 'csv' | 'pdf'

/** A student's past online submissions (read-only, plain status). */
export interface ApplicationHistoryItem {
  id: number
  declared_type: ApplicantDocType
  issuing_school: string
  status_text: string
  submitted_at: string | null
}

// Reference samples per issuing school (docs/applicant-intake.md, "References"). The labels
// are dataset labels; a match with a reference sample is not proof of authenticity.
export type ReferenceLabel = 'known_good_sample' | 'altered_sample' | 'other'
export type ReferenceUse = 'training' | 'held_out'
export type ReferenceStatus = 'queued' | 'processing' | 'pending' | 'approved' | 'retired' | 'failed'
export interface ReferenceRules {
  required_fields: string[]
  field_labels: Record<string, string[]>
  layout_tolerance: number
}
export interface ApplicantReference {
  id: number
  issuing_school: string
  school_key: string
  doc_type: ApplicantDocType | 'unknown'
  label: ReferenceLabel
  use: ReferenceUse
  version: number
  replaces_id: number | null
  status: ReferenceStatus
  status_reason: string | null
  rules: ReferenceRules | null
  pages: number
  attempts: number
  error: string | null
  created_by: string | null
  created_at: string
  decided_by: string | null
  decided_at: string | null
  read: boolean
  measured_pages: number
  reading?: { doc_type?: { predicted?: string; confidence?: number; model?: string }; fields?: Record<string, { value?: string | null }>; pages?: { page: number; ocr_text: string }[] } | null
  note?: string
}

/** Backend comparison of an online upload with the school's reference sample. */
export interface IntakeAssessment {
  reference: { id: number; version: number; issuing_school: string; doc_type: string; layout_tolerance: number; required_fields: string[] } | null
  findings: ({ code: string } & Record<string, unknown>)[]
  layout_model: { status: 'unavailable' | 'evaluated'; reason?: string; label?: ReferenceLabel; confidence?: number; version?: string; held_out_count?: number; held_out_accuracy?: number | null }
  reasons: string[]
}
export interface SecondPassView {
  status: SecondPassStatus
  attempts: number
  error: string | null
  created_at: string
  completed_at: string | null
  engine: string | null
  fields: Record<string, string | null> | null
  disagreements: { field: string; first: string | null; second: string | null }[]
}

// Per-student advisers (one teacher per student and term), distinct from class advisers (S-08).
export interface StudentAdviserRow {
  id: number
  student_id: number
  student_number: string
  student_name: string
  term_id: number
  term: string
  teacher_id: number
  teacher_name: string
  assigned_at: string
}
export interface AdviserOptions {
  students: { id: number; student_number: string; name: string }[]
  teachers: { id: number; name: string }[]
  terms: { id: number; name: string; is_current: boolean }[]
}
export interface MyAdviser { term_id: number; term: string; is_current: boolean; teacher_name: string; department: string | null }
export interface Advisee {
  student_id: number
  student_number: string
  student_name: string
  program_code: string | null
  year_level: number | null
  term_id: number
  term: string
  is_current: boolean
}

export interface IntakeThresholds {
  min_doc_type_confidence: number
  max_indicator_severity: number
  min_layout_score: number
  min_similarity_score: number
}
export interface IntakeSettings extends IntakeThresholds { defaults: IntakeThresholds; updated_at: string | null }

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, message: string, detail: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

function messageOf(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown })?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ') || fallback
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: string }).message)
  return fallback
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData
  const res = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    ...init,
    headers: isForm ? init.headers : { 'Content-Type': 'application/json', ...init.headers },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, messageOf(body, res.statusText), body)
  }
  if (res.status === 204) return undefined as T
  const type = res.headers.get('content-type') ?? ''
  return (type.includes('json') ? await res.json() : await res.blob()) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body: JSON.stringify(body) })
const patch = <T>(path: string, body: unknown) => request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
const qs = (params: Record<string, string | number | undefined | null>) => {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  return entries.length ? '?' + new URLSearchParams(entries.map(([k, v]) => [k, String(v)])).toString() : ''
}

const upload = <T>(path: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return request<T>(path, { method: 'POST', body: form })
}

export const api = {
  notifications: () => request<Notification[]>('/notifications'),
  readNotifications: (ids: number[]) => post('/notifications/read', { ids }),
  mechanismStatus: () => request<MechanismStatus>('/mechanism/status'),
  mechanismCommand: (body: MechanismCommand) => post<MechanismStatus>('/mechanism/command',
    // Older local services require these protocol fields before starting a scan.
    // Supply the station's fixed positions directly, without calibration UI or retries.
    body.action === 'scan' ? { ...body, valid_angle: 180, invalid_angle: 90, calibrated: true } : body),
  // auth
  me: () => request<User>('/auth/me'),
  login: (email: string, password: string) => post<User>('/auth/login', { email, password }),
  logout: () => post<void>('/auth/logout'),
  changePassword: (current_password: string, new_password: string) =>
    post<void>('/auth/change-password', { current_password, new_password }),

  // student
  eligibility: () => request<Record<DocType, string[]>>('/requests/eligibility'),
  myRecords: () => request<TermRecord[]>('/students/me/records'),

  // requests
  requests: (params: { status?: RequestStatus; client_status?: ClientStatus; document_type?: DocType } = {}) =>
    request<DocRequest[]>(`/requests${qs(params)}`),
  requestDetail: (id: number) => request<DocRequestDetail>(`/requests/${id}`),
  submitRequest: (body: { document_type: DocType; purpose: string; copies: number; remarks?: string; student_id?: number }) =>
    post<DocRequest>('/requests', body),
  cancelRequest: (id: number, reason?: string) =>
    post<DocRequest>(`/requests/${id}/cancel`, reason ? { reason } : undefined),
  approve: (id: number) => post<DocRequest>(`/requests/${id}/approve`),
  verify: (id: number) => post<DocRequest>(`/requests/${id}/verify`),
  release: (id: number) => post<DocRequest>(`/requests/${id}/release`),
  reject: (id: number, reason: string) => post<DocRequest>(`/requests/${id}/reject`, { reason }),
  regenerate: (id: number, reason: string) => post<DocRequest>(`/requests/${id}/regenerate`, { reason }),
  confirmPayment: (id: number) => post<DocRequest>(`/requests/${id}/confirm-payment`),
  downloadLink: (documentId: number) => post<{ url: string; expires_at: string }>(`/documents/${documentId}/download-link`),
  revoke: (documentId: number, reason: string) => post(`/documents/${documentId}/revoke`, { reason }),
  reopenDownload: (documentId: number, reason: string) => post(`/documents/${documentId}/reopen-download`, { reason }),
  documentTimeline: (documentId: number) =>
    request<{ timeline: TimelineEntry[] }>(`/documents/${documentId}`).then((d) => d.timeline),

  // registrar
  dashboard: (range: { date_from?: string; date_to?: string } = {}) =>
    request<RegistrarDashboard>(`/dashboard/registrar${qs(range)}`),
  auditLogs: (params: { q?: string; date_from?: string; date_to?: string; offset?: number; limit?: number }) =>
    request<{ total: number; items: AuditEntry[] }>(`/audit-logs${qs(params)}`),
  students: (q?: string) => request<StudentRow[]>(`/students${qs({ q })}`),
  createStudent: (body: Record<string, unknown>) => post<StudentRow>('/students', body),
  updateStudent: (id: number, body: Record<string, unknown> & { reason: string }) =>
    patch<StudentRow>(`/students/${id}`, body),
  createStudentLogin: (id: number, email: string, temporary_password: string) =>
    post<StudentRow>(`/students/${id}/account`, { email, temporary_password }),
  importStudents: (file: File) => upload<{ imported: number }>('/students/import', file),
  changeRequests: (status?: ChangeRequest['status']) =>
    request<ChangeRequest[]>(`/grade-change-requests${qs({ status })}`),
  approveChange: (id: number) => post<ChangeRequest>(`/grade-change-requests/${id}/approve`),
  rejectChange: (id: number, reason: string) => post<ChangeRequest>(`/grade-change-requests/${id}/reject`, { reason }),
  programs: () => request<Program[]>('/programs'),
  terms: () => request<TermRow[]>('/terms'),
  createProgram: (body: { code: string; name: string; department?: string | null; total_units?: number | null }) =>
    post<Program>('/programs', body),
  subjects: () => request<SubjectRow[]>('/subjects'),
  createSubject: (body: { code: string; title: string; units: number; program_id: number | null }) =>
    post<SubjectRow>('/subjects', body),
  createTerm: (academic_year: string, semester: string) => post<TermRow>('/terms', { academic_year, semester }),
  setCurrentTerm: (id: number) => post<TermRow>(`/terms/${id}/set-current`),
  teachers: () => request<Teacher[]>('/teachers'),
  createTeacher: (body: { employee_number: string; full_name: string; department?: string | null; email?: string; temporary_password?: string }) =>
    post<Teacher>('/teachers', body),
  allSections: () => request<SectionRow[]>('/sections'),
  createSection: (body: { subject_id: number; term_id: number; section_code: string; teacher_id: number | null }) =>
    post<SectionRow>('/sections', body),
  assignTeacher: (sectionId: number, teacher_id: number | null) =>
    patch<SectionRow>(`/sections/${sectionId}/teacher`, { teacher_id }),
  sectionAdvisers: (termId?: number) => request<AdviserRow[]>(`/section-advisers${qs({ term_id: termId })}`),
  setAdviser: (body: { term_id: number; section_code: string; teacher_id: number | null; reason?: string }) =>
    put<AdviserRow>('/section-advisers', body),
  enrollByNumber: (sectionId: number, student_numbers: string[]) =>
    post<{ enrolled: number }>(`/sections/${sectionId}/enroll`, { student_numbers }),
  lockTerm: (id: number, force: boolean) => post(`/terms/${id}/lock`, { force }),
  templates: () => request<Template[]>('/templates'),
  uploadTemplate: (body: { document_type: DocType; html_content: string; field_zones: Zones | null }) =>
    post<Template>('/templates', body),
  updateTemplate: (id: number, body: { html_content?: string; field_zones?: Zones | null }) =>
    patch<Template>(`/templates/${id}`, body),
  previewTemplate: (id: number, studentId?: number) =>
    request<Blob>(`/templates/${id}/preview${qs({ student_id: studentId })}`, { method: 'POST' }),
  activateTemplate: (id: number) => post<Template>(`/templates/${id}/activate`),

  // teacher
  sections: () => request<Section[]>('/sections'),
  advisory: () => request<AdvisoryClass[]>('/advisory'),
  sectionGrades: (id: number) => request<GradeRow[]>(`/sections/${id}/grades`),
  saveGrades: (id: number, entries: { enrollment_id: number; grade: string }[], reason?: string) =>
    put<{ saved: number }>(`/sections/${id}/grades`, { entries, reason }),
  submitSection: (id: number) => post(`/sections/${id}/submit`),
  importGrades: (id: number, file: File) => upload<{ saved: number }>(`/sections/${id}/grades/import`, file),
  requestGradeChange: (grade_id: number, new_grade: string, reason: string) =>
    post<ChangeRequest>('/grade-change-requests', { grade_id, new_grade, reason }),

  // operator
  stations: () => request<Station[]>('/scan-stations'),
  startSession: (mode: 'single' | 'bulk', station_id: number) =>
    post<{ session_id: number; mode: string; output_tray: string }>('/scan-sessions', { mode, station_id }),
  endSession: (id: number) => post(`/scan-sessions/${id}/end`),
  session: (id: number) => request<ScanSession>(`/scan-sessions/${id}`),
  recentScans: (filters: ScanFilters = {}) => request<ScanRow[]>(`/validations${qs({ ...filters, limit: 50 })}`),
  scanDetail: (id: number) => request<ScanDetail>(`/validations/${id}`),
  scanStats: (filters: ScanFilters = {}) =>
    request<{ results: Record<Result, number>; total: number; failed_fields: { field: string; count: number }[] }>(`/validations/stats${qs({ ...filters })}`),
  review: (id: number, final_result: 'VALID' | 'INVALID', remarks: string) =>
    post(`/validations/${id}/review`, { final_result, remarks }),
  exportUrl: (format: 'csv' | 'pdf', sessionId?: number, filters: ScanFilters = {}) =>
    `/api/validations/export${qs({ format, session_id: sessionId, ...filters })}`,

  // incoming documents (registrar review queue; thresholds are admin-only)
  intakeQueue: (filters: IntakeFilters = {}) => request<IntakeRow[]>(`/intake/documents${qs({ ...filters })}`),
  intakeDetail: (id: number) => request<IntakeDetail>(`/intake/documents/${id}`),
  intakeImageUrl: (id: number) => `/api/intake/documents/${id}/image`,
  reviewIntake: (id: number, action: IntakeAction, reason?: string) =>
    post<IntakeRow>(`/intake/documents/${id}/review`, reason ? { action, reason } : { action }),
  intakeFields: (id: number, docType?: IntakeDocType) =>
    request<IntakeFields>(`/intake/documents/${id}/fields${qs({ doc_type: docType })}`),
  saveIntakeFields: (id: number, values: Record<string, string | null>, docType?: IntakeDocType) =>
    put<IntakeFields>(`/intake/documents/${id}/fields`, docType ? { values, doc_type: docType } : { values }),
  intakeCandidates: (id: number) =>
    request<{ candidates: IntakeCandidate[] }>(`/intake/documents/${id}/candidates`).then((r) => r.candidates),
  linkIntake: (id: number, student_id: number, reason?: string) =>
    post<IntakeFields>(`/intake/documents/${id}/link`, reason ? { student_id, reason } : { student_id }),
  createStudentFromIntake: (id: number, body: { student_number: string; program_id?: number | null; year_level?: number | null; confirm_new?: boolean }) =>
    post<IntakeFields>(`/intake/documents/${id}/create-student`, body),
  unlinkIntake: (id: number, reason: string) => post<IntakeFields>(`/intake/documents/${id}/unlink`, { reason }),
  student: (id: number) => request<StudentRow>(`/students/${id}`),
  intakeSettings: () => request<IntakeSettings>('/intake/settings'),
  saveIntakeSettings: (body: IntakeThresholds & { reason: string }) => put<IntakeSettings>('/intake/settings', body),

  // applicant intake: the applicant's own submissions
  mySubmissions: () => request<ApplicantSubmission[]>('/applicant/submissions'),
  startSubmission: (body: { declared_type: ApplicantDocType; issuing_school: string; consent: boolean }) =>
    post<ApplicantSubmission>('/applicant/submissions', body),
  uploadSubmissionFile: (id: number, file: File) =>
    upload<ApplicantSubmission>(`/applicant/submissions/${id}/pages`, file),
  deleteSubmissionPage: (id: number, page: number) =>
    request<ApplicantSubmission>(`/applicant/submissions/${id}/pages/${page}`, { method: 'DELETE' }),
  submitSubmission: (id: number) => post<ApplicantSubmission>(`/applicant/submissions/${id}/submit`),
  withdrawSubmission: (id: number) => post<ApplicantSubmission>(`/applicant/submissions/${id}/withdraw`),
  mySubmissionPageUrl: (id: number, page: number) => `/api/applicant/submissions/${id}/pages/${page}`,
  // applicant intake: staff
  createApplicant: (email: string, temporary_password: string) =>
    post<{ id: number; email: string }>('/applicants', { email, temporary_password }),
  applicantSubmissions: (state?: ApplicantSubmissionStatus | '') =>
    request<StaffApplicantSubmission[]>(`/applicant-submissions${qs({ state })}`),
  retrySubmission: (id: number, reason: string) =>
    post<StaffApplicantSubmission>(`/applicant-submissions/${id}/retry`, { reason }),
  applicantPageUrl: (id: number, page: number) => `/api/applicant-submissions/${id}/pages/${page}`,
  submissionReportUrl: (id: number, format: ReportFormat) => `/api/applicant-submissions/${id}/report${qs({ format })}`,
  // ... on an applicant login's behalf (audited)
  staffStartSubmission: (body: { applicant_id: number; declared_type: ApplicantDocType; issuing_school: string; consent: boolean }) =>
    post<StaffApplicantSubmission>('/applicant-submissions', body),
  staffUploadPage: (id: number, file: File) => upload<StaffApplicantSubmission>(`/applicant-submissions/${id}/pages`, file),
  staffDeletePage: (id: number, page: number) =>
    request<StaffApplicantSubmission>(`/applicant-submissions/${id}/pages/${page}`, { method: 'DELETE' }),
  staffSubmit: (id: number) => post<StaffApplicantSubmission>(`/applicant-submissions/${id}/submit`),
  applicantLogins: () => request<User[]>('/users?role=applicant'),
  // reference samples
  references: (state?: ReferenceStatus | '') => request<ApplicantReference[]>(`/applicant-references${qs({ state })}`),
  reference: (id: number) => request<ApplicantReference>(`/applicant-references/${id}`),
  uploadReference: (form: FormData) => request<ApplicantReference>('/applicant-references', { method: 'POST', body: form }),
  editReference: (id: number, body: { label?: ReferenceLabel; use?: ReferenceUse; rules?: ReferenceRules; reason?: string }) =>
    patch<ApplicantReference>(`/applicant-references/${id}`, body),
  approveReference: (id: number, reason: string) => post<ApplicantReference>(`/applicant-references/${id}/approve`, { reason }),
  retireReference: (id: number, reason: string) => post<ApplicantReference>(`/applicant-references/${id}/retire`, { reason }),
  retryReference: (id: number, reason: string) => post<ApplicantReference>(`/applicant-references/${id}/retry`, { reason }),
  referencePageUrl: (id: number, page: number) => `/api/applicant-references/${id}/pages/${page}`,
  // per-student advisers
  studentAdvisers: (termId?: number) => request<StudentAdviserRow[]>(`/student-advisers${qs({ term_id: termId })}`),
  adviserOptions: () => request<AdviserOptions>('/student-advisers/options'),
  setStudentAdviser: (body: { student_id: number; term_id: number; teacher_id: number | null; reason: string }) =>
    put<StudentAdviserRow | null>('/student-advisers', body),
  myAdvisers: () => request<MyAdviser[]>('/students/me/advisers'),
  advisees: () => request<Advisee[]>('/advisees'),
  myApplications: () => request<ApplicationHistoryItem[]>('/students/me/applications'),

  // admin
  users: () => request<User[]>('/users'),
  createUser: (body: { email: string; role: Role; temporary_password: string; can_validate: boolean }) =>
    post<User>('/users', body),
  resetPassword: (id: number, temporary_password: string, reason: string) =>
    post<void>(`/users/${id}/reset-password`, { temporary_password, reason }),
  deactivateUser: (id: number, reason: string) => post<User>(`/users/${id}/deactivate`, { reason }),
  adminStations: () => request<{ id: number; name: string; is_active: boolean; created_at: string }[]>('/stations'),
  registerStation: (name: string) => post<{ id: number; name: string; api_key: string }>('/stations', { name }),
  deactivateStation: (id: number, reason: string) => post(`/stations/${id}/deactivate`, { reason }),
}
