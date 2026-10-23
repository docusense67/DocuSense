import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiError, type ApplicantDocType, type ApplicantSubmission } from '../../api'
import { Button, Card, Empty, ErrorText, Field, Page, Select } from '../../components/ui'
import { formatDate } from '../../lib/format'

// docs/applicant-intake.md. Documents from another school, read and then reviewed by the
// registrar. The applicant sees one plain status per submission, never staff notes.

const TYPE_LABEL: Record<ApplicantDocType, string> = {
  transcript_of_records: 'Transcript of Records (TOR)',
  certificate_of_enrollment: 'Certificate of Enrollment (COE)',
}
const MAX_UPLOAD_BYTES = 4_000_000 // one request per file: the web proxy refuses bigger bodies
const ACCEPT = 'application/pdf,image/png,image/jpeg'

const PRIVACY_NOTICE =
  'The school uses the pages you upload, and the details read from them, only to process your ' +
  'admission. The files are stored privately; only registrar staff can open them. Ask the ' +
  "registrar's office how long they are kept and how to have them removed."

function NewSubmission() {
  const qc = useQueryClient()
  const [type, setType] = useState<ApplicantDocType | ''>('')
  const [school, setSchool] = useState('')
  const [consent, setConsent] = useState(false)
  const start = useMutation({
    mutationFn: () => api.startSubmission({ declared_type: type as ApplicantDocType, issuing_school: school, consent }),
    onSuccess: () => {
      setType(''); setSchool(''); setConsent(false)
      qc.invalidateQueries({ queryKey: ['my-submissions'] })
    },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); start.mutate() }

  return (
    <Card title="Send a document from your previous school">
      <form onSubmit={onSubmit} className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <Select label="Document" required value={type} onChange={(e) => setType(e.target.value as ApplicantDocType)}>
            <option value="">Choose…</option>
            {(Object.keys(TYPE_LABEL) as ApplicantDocType[]).map((t) => <option key={t} value={t}>{TYPE_LABEL[t]}</option>)}
          </Select>
          <Field label="School that issued it" required minLength={2} maxLength={200} value={school}
            onChange={(e) => setSchool(e.target.value)} />
        </div>
        <p className="rounded-md bg-surface-2 p-3 text-xs text-ink-2">{PRIVACY_NOTICE}</p>
        <label className="flex items-start gap-2 text-sm text-ink">
          <input type="checkbox" className="mt-0.5" checked={consent} required data-testid="applicant-consent"
            onChange={(e) => setConsent(e.target.checked)} />
          I have read this notice and agree to the processing of my document.
        </label>
        <Button type="submit" busy={start.isPending} disabled={!type || !consent}>Start</Button>
        <ErrorText error={start.error} />
      </form>
    </Card>
  )
}

function Pages({ submission, editable }: { submission: ApplicantSubmission; editable: boolean }) {
  const qc = useQueryClient()
  const remove = useMutation({
    mutationFn: (page: number) => api.deleteSubmissionPage(submission.id, page),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-submissions'] }),
  })
  if (submission.pages === 0) return <p className="text-sm text-ink-2">No pages yet.</p>
  // Versioned with the page count so a removed page's image is not served from cache.
  return (
    <div className="flex flex-wrap gap-3">
      {Array.from({ length: submission.pages }, (_, i) => i + 1).map((page) => (
        <figure key={`${page}-${submission.pages}`} className="w-28 text-center text-xs text-ink-2">
          <img src={`${api.mySubmissionPageUrl(submission.id, page)}?v=${submission.pages}`} alt={`Page ${page}`}
            className="h-36 w-28 rounded border border-line bg-white object-contain" loading="lazy" />
          <figcaption className="mt-1">
            Page {page}
            {editable && (
              <button type="button" className="ml-2 text-critical hover:underline" disabled={remove.isPending}
                onClick={() => remove.mutate(page)}>Remove</button>
            )}
          </figcaption>
        </figure>
      ))}
      <ErrorText error={remove.error} />
    </div>
  )
}

function Upload({ submission }: { submission: ApplicantSubmission }) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [problems, setProblems] = useState<string[]>([])
  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    const found: string[] = []
    for (const file of Array.from(files)) { // one at a time, in the order chosen
      if (file.size > MAX_UPLOAD_BYTES) {
        found.push(`${file.name}: larger than 4 MB. Upload the pages one at a time or scan at a lower resolution.`)
        continue
      }
      try {
        await api.uploadSubmissionFile(submission.id, file)
      } catch (e) {
        found.push(`${file.name}: ${e instanceof ApiError ? e.message : 'upload failed'}`)
      }
    }
    setProblems(found)
    setBusy(false)
    qc.invalidateQueries({ queryKey: ['my-submissions'] })
  }
  return (
    <div>
      <label className="block text-sm text-ink-2">Add pages (PDF, PNG or JPEG; a PDF adds all its pages)
        <input type="file" accept={ACCEPT} multiple disabled={busy} className="mt-1 block w-full text-sm"
          data-testid="applicant-upload" onChange={(e) => { void onFiles(e.target.files); e.target.value = '' }} />
      </label>
      {busy && <p className="mt-1 text-xs text-ink-2">Uploading…</p>}
      {problems.map((p) => <p key={p} className="mt-1 text-xs text-critical">{p}</p>)}
    </div>
  )
}

function SubmissionCard({ submission }: { submission: ApplicantSubmission }) {
  const qc = useQueryClient()
  const done = () => qc.invalidateQueries({ queryKey: ['my-submissions'] })
  const submit = useMutation({ mutationFn: () => api.submitSubmission(submission.id), onSuccess: done })
  const withdraw = useMutation({ mutationFn: () => api.withdrawSubmission(submission.id), onSuccess: done })
  const draft = submission.status === 'draft'
  return (
    <Card title={`${TYPE_LABEL[submission.declared_type]} · ${submission.issuing_school}`}
      actions={<span className="text-sm font-medium text-ink" data-testid="applicant-status">{submission.status_text}</span>}>
      <div className="space-y-3">
        <p className="text-xs text-ink-2">
          Started {formatDate(submission.created_at)}
          {submission.submitted_at && <> · sent {formatDate(submission.submitted_at)}</>}
        </p>
        {submission.status !== 'withdrawn' && <Pages submission={submission} editable={draft} />}
        {draft && <Upload submission={submission} />}
        <div className="flex gap-2">
          {draft && <Button busy={submit.isPending} disabled={submission.pages === 0}
            onClick={() => submit.mutate()}>Send to the registrar</Button>}
          {(draft || submission.status === 'queued') && (
            <Button variant="secondary" busy={withdraw.isPending} onClick={() => withdraw.mutate()}>Withdraw</Button>
          )}
        </div>
        <ErrorText error={submit.error ?? withdraw.error} />
      </div>
    </Card>
  )
}

export function ApplicantHome() {
  const submissions = useQuery({ queryKey: ['my-submissions'], queryFn: api.mySubmissions, refetchInterval: 15_000 })
  return (
    <Page title="My documents">
      <NewSubmission />
      {submissions.error && <ErrorText error={submissions.error} />}
      {submissions.data?.length === 0 && <Empty>Nothing sent yet.</Empty>}
      {submissions.data?.map((s) => <SubmissionCard key={s.id} submission={s} />)}
    </Page>
  )
}
