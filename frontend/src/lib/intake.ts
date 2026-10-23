import type { IntakeDecision, IntakeDocType, IntakeStatus, ReferenceLabel, ReferenceStatus, ReferenceUse, SecondPassStatus } from '../api'

// Wording rule (screening contract): "passed screening" / "needs manual review".
// Never "authentic", "genuine" or "verified". One exception: the dataset labels of reference
// samples ("Genuine sample" / "Altered sample"), always shown with REFERENCE_NOTE.

export const REFERENCE_NOTE =
  'Reference samples are the school’s own labelled examples. A match with a reference sample is not proof of ' +
  'authenticity: it only means no automatic check flagged the upload. Staff make the decision.'

export const REFERENCE_LABEL: Record<ReferenceLabel, string> = {
  genuine_sample: 'Genuine sample (dataset label)',
  altered_sample: 'Altered sample (dataset label)',
  other: 'Other document (not a TOR/COE)',
}

export const REFERENCE_USE: Record<ReferenceUse, string> = {
  training: 'Training (compared with, trains the models)',
  held_out: 'Held-out (evaluation only)',
}

export const REFERENCE_STATUS: Record<ReferenceStatus, string> = {
  queued: 'Waiting to be read',
  processing: 'Being read',
  pending: 'Read: waiting for approval',
  approved: 'Approved',
  retired: 'Retired',
  failed: 'Could not be read',
}

export const SECOND_PASS_STATUS: Record<SecondPassStatus, string> = {
  queued: 'Second reading queued',
  processing: 'Second reading in progress',
  done: 'Second reading done',
  failed: 'Second reading failed',
}

/** Findings of the comparison with the reference sample (backend services/applicant_references). */
export const FINDING_LABEL: Record<string, string> = {
  reference_field_missing: 'Field missing',
  layout_not_measured: 'Layout not measured',
  page_count_differs: 'Page count differs',
  layout_shifted: 'Heading or label moved',
  reference_regions_missing: 'Headings or labels not found',
  text_spacing_differs: 'Text size or spacing differs',
}

export const DOC_TYPE_LABEL: Record<IntakeDocType, string> = {
  psa_birth_certificate: 'PSA birth certificate',
  jhs_report_card: 'JHS report card',
  good_moral_certificate: 'Good moral certificate',
  jhs_completion_certificate: 'JHS completion certificate',
  transcript_of_records: 'Transcript of Records (other school)',
  certificate_of_enrollment: 'Certificate of Enrollment (other school)',
  unknown: 'Unknown document',
}

export const DECISION_LABEL: Record<IntakeDecision, string> = {
  passed_screening: 'Passed screening',
  manual_review: 'Needs manual review',
}

export const STATUS_LABEL: Record<IntakeStatus, string> = {
  screened: 'Waiting for review',
  accepted: 'Accepted',
  rejected: 'Rejected',
  resubmission_requested: 'Resubmission requested',
  data_reviewed: 'Data reviewed',
  linked: 'Linked to student',
}

/** Why a suggested student matches the reviewed fields (backend services/intake_fields). */
export const MATCH_LABEL: Record<string, string> = {
  lrn: 'Same LRN',
  name_birthdate: 'Same name and birth date',
  name: 'Same name (no birth date to compare)',
  name_birthdate_differs: 'Same name, different birth date',
  first_name_birthdate: 'Same first name and birth date, different last name',
}

export const CONFLICT_LABEL: Record<string, string> = {
  lrn_differs: 'LRN differs',
  name_differs: 'name differs',
  birthdate_differs: 'birth date differs',
}

/** What each suspicious-feature code from the station means, in plain language. */
export const INDICATOR_LABEL: Record<string, string> = {
  missing_heading: 'A heading this document should have is missing',
  missing_field: 'A field this document should have is missing',
  field_out_of_position: 'A field is not where it usually is',
  seal_logo_not_found: 'The expected seal or logo was not found',
  font_inconsistent: 'Text sizes within a field do not match',
  alignment_off: 'Text alignment is off',
  unexpected_text: 'Unexpected text on the page',
  altered_region: 'An area of the page may have been altered',
}

export const indicatorLabel = (code: string | undefined) =>
  (code && INDICATOR_LABEL[code]) || (code ? code.replaceAll('_', ' ') : 'Unnamed indicator')

export const percent = (value: unknown) =>
  typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—'

export const QUALITY_ISSUE_LABEL: Record<string, string> = {
  blur: 'blurred', too_dark: 'too dark', too_bright: 'too bright', glare: 'glare', skew: 'skewed',
  page_not_found: 'page not found', page_cut_off: 'page cut off', low_resolution: 'low resolution',
}
