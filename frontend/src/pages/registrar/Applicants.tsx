import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ApplicantSubmissionStatus, type StaffApplicantSubmission } from '../../api'
import { Button, Card, Empty, ErrorText, Field, Page, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { DOC_TYPE_LABEL } from '../../lib/intake'

// docs/applicant-intake.md: applicant logins (no public sign-up) and their online uploads.
// A read upload is reviewed in Incoming documents like a page from the intake station.

const STATE_LABEL: Record<ApplicantSubmissionStatus, string> = {
  draft: 'Draft (applicant adding pages)',
  queued: 'Waiting to be read',
  processing: 'Being read',
  screened: 'Read: in Incoming documents',
  failed: 'Could not be read',
  withdrawn: 'Withdrawn',
}

function CreateApplicant() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const create = useMutation({
    mutationFn: () => api.createApplicant(email, password),
    onSuccess: () => { setEmail(''); setPassword(''); qc.invalidateQueries({ queryKey: ['users'] }) },
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
      <CreateApplicant />
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
                <Td>{s.applicant_email ?? '—'}</Td>
                <Td>{DOC_TYPE_LABEL[s.declared_type]}<span className="block text-xs text-muted">{s.issuing_school}</span></Td>
                <Td>{s.pages}</Td>
                <Td>{formatDate(s.submitted_at, true)}</Td>
                <Td>
                  {s.intake_document_id
                    ? <Link to={`/intake/${s.intake_document_id}`} className="text-accent hover:underline">{STATE_LABEL[s.status]}</Link>
                    : STATE_LABEL[s.status]}
                  {s.status === 'failed' && (
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
