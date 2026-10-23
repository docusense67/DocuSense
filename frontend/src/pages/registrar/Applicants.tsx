import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, ApiError, type ApplicantDocType, type ApplicantSubmissionStatus, type ReportFormat, type StaffApplicantSubmission,
} from '../../api'
import { Button, Card, Empty, ErrorText, Field, Page, Select, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { DOC_TYPE_LABEL, SECOND_PASS_STATUS } from '../../lib/intake'

// docs/applicant-intake.md: applicant logins (no public sign-up) and their online uploads.
// A read upload is reviewed in Incoming documents like a page from the intake station.

const STATE_LABEL: Record<ApplicantSubmissionStatus, string> = {
  draft: 'Draft (pages being added)',
  queued: 'Waiting to be read',
  processing: 'Being read',
  screened: 'Read: in Incoming documents',
  failed: 'Could not be read',
  withdrawn: 'Withdrawn',
}
const MAX_UPLOAD_BYTES = 4_000_000 // one request per file: the web proxy refuses bigger bodies

function CreateApplicant() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const create = useMutation({
    mutationFn: () => api.createApplicant(email, password),
    onSuccess: () => {
      setEmail(''); setPassword('')
      qc.invalidateQueries({ queryKey: ['users'] })
      qc.invalidateQueries({ queryKey: ['applicant-logins'] })
    },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); create.mutate() }
  return (
    <Card title="Create an applicant login">
      <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-[2fr_2fr_auto] sm:items-end">
        <Field label="Applicant's email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label="Temporary password" type="password" required minLength={10} value={password}
          hint="They must change it at first login." onChange={(e) => setPassword(e.target.value)} />
        <Button type="submit" busy={create.isPending}>Create</Button>
      </form>
      {create.data && <p className="mt-2 text-sm text-good">Login created for {create.data.email}. Give them the temporary password in person.</p>}
      <ErrorText error={create.error} />
    </Card>
  )
}

/** Documents an applicant brought in person: staff start the submission for their login. */
function UploadForApplicant() {
  const qc = useQueryClient()
  const logins = useQuery({ queryKey: ['applicant-logins'], queryFn: api.applicantLogins })
  const [applicantId, setApplicantId] = useState('')
  const [type, setType] = useState<ApplicantDocType | ''>('')
  const [school, setSchool] = useState('')
  const [consent, setConsent] = useState(false)
  const start = useMutation({
    mutationFn: () => api.staffStartSubmission({
      applicant_id: Number(applicantId), declared_type: type as ApplicantDocType, issuing_school: school, consent,
    }),
    onSuccess: () => {
      setType(''); setSchool(''); setConsent(false)
      qc.invalidateQueries({ queryKey: ['applicant-submissions'] })
    },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); start.mutate() }
  const active = (logins.data ?? []).filter((u) => u.is_active)
  return (
    <Card title="Upload for an applicant">
      <form onSubmit={onSubmit} className="space-y-3" data-testid="staff-upload">
        <div className="grid gap-3 sm:grid-cols-3">
          <Select label="Applicant login" required value={applicantId} onChange={(e) => setApplicantId(e.target.value)}>
            <option value="">Choose…</option>
            {active.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}
          </Select>
          <Select label="Document" required value={type} onChange={(e) => setType(e.target.value as ApplicantDocType)}>
            <option value="">Choose…</option>
            <option value="transcript_of_records">{DOC_TYPE_LABEL.transcript_of_records}</option>
            <option value="certificate_of_enrollment">{DOC_TYPE_LABEL.certificate_of_enrollment}</option>
          </Select>
          <Field label="School that issued it" required minLength={2} maxLength={200} value={school}
            onChange={(e) => setSchool(e.target.value)} />
        </div>
        <label className="flex items-start gap-2 text-sm text-ink">
          <input type="checkbox" className="mt-0.5" checked={consent} required onChange={(e) => setConsent(e.target.checked)} />
          The applicant read the privacy notice and agreed to the processing of this document.
        </label>
        <p className="text-xs text-muted">The submission is recorded as uploaded by you. Add the pages below, then send it to be read.</p>
        <Button type="submit" busy={start.isPending} disabled={!applicantId || !type || !consent}>Start</Button>
        <ErrorText error={start.error ?? logins.error} />
      </form>
    </Card>
  )
}

function DraftControls({ s }: { s: StaffApplicantSubmission }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [problems, setProblems] = useState<string[]>([])
  const done = () => qc.invalidateQueries({ queryKey: ['applicant-submissions'] })
  const submit = useMutation({ mutationFn: () => api.staffSubmit(s.id), onSuccess: done })
  const remove = useMutation({ mutationFn: (page: number) => api.staffDeletePage(s.id, page), onSuccess: done })
  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    const found: string[] = []
    for (const file of Array.from(files)) {
      if (file.size > MAX_UPLOAD_BYTES) { found.push(`${file.name}: larger than 4 MB.`); continue }
      try { await api.staffUploadPage(s.id, file) } catch (e) {
        found.push(`${file.name}: ${e instanceof ApiError ? e.message : 'upload failed'}`)
      }
    }
    setProblems(found)
    setBusy(false)
    done()
  }
  return (
    <div className="mt-2 space-y-2" data-testid="staff-draft">
      <div className="flex flex-wrap gap-2 text-xs">
        {Array.from({ length: s.pages }, (_, i) => i + 1).map((page) => (
          <span key={`${page}-${s.pages}`} className="rounded border border-line px-1.5 py-0.5">
            <a href={api.applicantPageUrl(s.id, page)} target="_blank" rel="noopener" className="text-accent hover:underline">Page {page}</a>
            <button type="button" className="ml-1 text-critical hover:underline" disabled={remove.isPending}
              onClick={() => remove.mutate(page)}>Remove</button>
          </span>
        ))}
      </div>
      <input type="file" accept="application/pdf,image/png,image/jpeg" multiple disabled={busy} aria-label={`Add pages to submission ${s.id}`}
        className="block w-full text-xs" onChange={(e) => { void onFiles(e.target.files); e.target.value = '' }} />
      {problems.map((p) => <p key={p} className="text-xs text-critical">{p}</p>)}
      <Button variant="secondary" busy={submit.isPending} disabled={s.pages === 0 || busy} onClick={() => submit.mutate()}>Send to be read</Button>
      <ErrorText error={submit.error ?? remove.error} />
    </div>
  )
}

function Retry({ s }: { s: StaffApplicantSubmission }) {
  const qc = useQueryClient()
  const [reason, setReason] = useState('')
  const retry = useMutation({
    mutationFn: () => api.retrySubmission(s.id, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['applicant-submissions'] }),
  })
  return (
    <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); retry.mutate() }}>
      <Field label="Reason to read it again" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button type="submit" variant="secondary" busy={retry.isPending}>Queue again</Button>
      <ErrorText error={retry.error} />
    </form>
  )
}

export function ReportLinks({ id }: { id: number }) {
  const formats: ReportFormat[] = ['json', 'csv', 'pdf']
  return (
    <span className="text-xs text-ink-2" data-testid="report-links">Report:{' '}
      {formats.map((f, i) => (
        <span key={f}>{i > 0 && ' · '}<a href={api.submissionReportUrl(id, f)} className="text-accent hover:underline">{f.toUpperCase()}</a></span>
      ))}
    </span>
  )
}

export function Applicants() {
  const [state, setState] = useState<ApplicantSubmissionStatus | ''>('')
  const list = useQuery({ queryKey: ['applicant-submissions', state], queryFn: () => api.applicantSubmissions(state),
    refetchInterval: 15_000 })
  const rows = list.data ?? []
  return (
    <Page title="Applicants">
      <p className="text-sm text-ink-2">
        Applicants are not students yet. They upload a Transcript of Records or Certificate of Enrollment from their
        previous school; the station's OCR worker reads it, and it then appears in Incoming documents for your review.
        Linking it to a student turns the applicant's login into that student's login.
      </p>
      <div className="grid gap-5 lg:grid-cols-2">
        <CreateApplicant />
        <UploadForApplicant />
      </div>
      <Card title="Uploads" actions={
        <select aria-label="Filter by state" value={state} onChange={(e) => setState(e.target.value as ApplicantSubmissionStatus | '')}
          className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink">
          <option value="">All states</option>
          {(Object.keys(STATE_LABEL) as ApplicantSubmissionStatus[]).map((s) => <option key={s} value={s}>{STATE_LABEL[s]}</option>)}
        </select>
      }>
        <ErrorText error={list.error} />
        {list.isPending ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No uploads.</Empty> : (
          <Table head={['#', 'Applicant', 'Document', 'Pages', 'Sent', 'State']}>
            {rows.map((s) => (
              <tr key={s.id} data-testid="applicant-submission-row">
                <Td>{s.id}</Td>
                <Td>{s.applicant_email ?? '—'}
                  {s.uploaded_by && <span className="block text-xs text-muted">uploaded by {s.uploaded_by}</span>}</Td>
                <Td>{DOC_TYPE_LABEL[s.declared_type]}<span className="block text-xs text-muted">{s.issuing_school}</span></Td>
                <Td>{s.pages}</Td>
                <Td>{formatDate(s.submitted_at, true)}</Td>
                <Td>
                  {s.intake_document_id
                    ? <Link to={`/intake/${s.intake_document_id}`} className="text-accent hover:underline">{STATE_LABEL[s.status]}</Link>
                    : STATE_LABEL[s.status]}
                  {s.second_pass_status && (
                    <span className="block text-xs text-ink-2">{SECOND_PASS_STATUS[s.second_pass_status]}
                      {s.second_pass_disagreements > 0 && <span className="font-semibold text-critical"> · ! {s.second_pass_disagreements} field(s) disagree</span>}
                    </span>
                  )}
                  {s.status === 'draft' && <DraftControls s={s} />}
                  {s.status !== 'draft' && s.status !== 'withdrawn' && <span className="block"><ReportLinks id={s.id} /></span>}
                  {(s.status === 'failed' || s.second_pass_status === 'failed') && (
                    <div className="mt-1 space-y-1">
                      <span className="block text-xs text-critical">{s.error}</span>
                      <Retry s={s} />
                    </div>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  )
}
