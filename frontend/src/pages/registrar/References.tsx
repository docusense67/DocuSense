import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import {
  api, type ApplicantReference, type ReferenceLabel, type ReferenceRules, type ReferenceStatus, type ReferenceUse,
} from '../../api'
import { ReasonButton } from '../../components/forms'
import { Button, Card, Empty, ErrorText, Field, Page, Select, Table, Td, TextArea } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { DOC_TYPE_LABEL, REFERENCE_LABEL, REFERENCE_NOTE, REFERENCE_STATUS, REFERENCE_USE, percent } from '../../lib/intake'

// docs/applicant-intake.md, "References": labelled samples of other schools' TOR/COE. The worker
// reads each version; staff approve the reading. Approved versions can't be edited: upload a new
// version and retire the old one.

const MAX_UPLOAD_BYTES = 4_000_000 // one request: the web proxy refuses bigger bodies
type RefDocType = 'transcript_of_records' | 'certificate_of_enrollment' | 'unknown'

/** "field: label one; label two" per line -> {field: [labels]} */
function parseLabels(text: string): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const line of text.split('\n')) {
    const colon = line.indexOf(':')
    const field = colon > 0 ? line.slice(0, colon).trim() : ''
    if (!field) continue
    const labels = line.slice(colon + 1).split(';').map((l) => l.trim()).filter(Boolean)
    if (labels.length) out[field] = labels
  }
  return out
}

const showLabels = (labels: Record<string, string[]>) =>
  Object.entries(labels).map(([field, list]) => `${field}: ${list.join('; ')}`).join('\n')

function UploadReference({ references }: { references: ApplicantReference[] }) {
  const qc = useQueryClient()
  const [school, setSchool] = useState('')
  const [docType, setDocType] = useState<RefDocType>('transcript_of_records')
  const [label, setLabel] = useState<ReferenceLabel>('known_good_sample')
  const [use, setUse] = useState<ReferenceUse>('training')
  const [required, setRequired] = useState('student_name')
  const [labels, setLabels] = useState('')
  const [tolerance, setTolerance] = useState('0.15')
  const [replaces, setReplaces] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const size = files.reduce((sum, f) => sum + f.size, 0)
  const create = useMutation({
    mutationFn: () => {
      const rules: ReferenceRules = {
        required_fields: required.split(',').map((f) => f.trim()).filter(Boolean),
        field_labels: parseLabels(labels),
        layout_tolerance: Number(tolerance),
      }
      const form = new FormData()
      form.append('issuing_school', school)
      form.append('doc_type', docType)
      form.append('label', label)
      form.append('use', use)
      form.append('rules', JSON.stringify(rules))
      if (replaces) form.append('replaces_id', replaces)
      for (const file of files) form.append('file', file)
      return api.uploadReference(form)
    },
    onSuccess: () => { setFiles([]); setReplaces(''); qc.invalidateQueries({ queryKey: ['references'] }) },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); create.mutate() }
  const sameSchool = references.filter((r) => r.issuing_school.toLowerCase() === school.trim().toLowerCase() && r.doc_type === docType)

  return (
    <Card title="Upload a reference sample (new version)">
      <form onSubmit={onSubmit} className="space-y-3" data-testid="reference-upload">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Issuing school" required minLength={2} maxLength={200} value={school} onChange={(e) => setSchool(e.target.value)} />
          <Select label="Document type" value={docType} onChange={(e) => {
            const value = e.target.value as RefDocType
            setDocType(value)
            setLabel(value === 'unknown' ? 'other' : label === 'other' ? 'known_good_sample' : label)
          }}>
            <option value="transcript_of_records">{DOC_TYPE_LABEL.transcript_of_records}</option>
            <option value="certificate_of_enrollment">{DOC_TYPE_LABEL.certificate_of_enrollment}</option>
            <option value="unknown">Other document (unknown class)</option>
          </Select>
          <Select label="Label" value={label} onChange={(e) => setLabel(e.target.value as ReferenceLabel)}>
            {(Object.keys(REFERENCE_LABEL) as ReferenceLabel[])
              .filter((l) => (docType === 'unknown') === (l === 'other'))
              .map((l) => <option key={l} value={l}>{REFERENCE_LABEL[l]}</option>)}
          </Select>
          <Select label="Use" value={use} onChange={(e) => setUse(e.target.value as ReferenceUse)}>
            {(Object.keys(REFERENCE_USE) as ReferenceUse[]).map((u) => <option key={u} value={u}>{REFERENCE_USE[u]}</option>)}
          </Select>
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Required fields (comma-separated)" value={required} onChange={(e) => setRequired(e.target.value)}
            hint="Field names, e.g. student_name, student_number, program." />
          <Field label="Layout tolerance" type="number" min={0.02} max={0.5} step={0.01} value={tolerance}
            onChange={(e) => setTolerance(e.target.value)} hint="How far (fraction of the page) a heading may move." />
          <Select label="Replaces version" value={replaces} onChange={(e) => setReplaces(e.target.value)}>
            <option value="">—</option>
            {sameSchool.map((r) => <option key={r.id} value={r.id}>#{r.id} · version {r.version} ({REFERENCE_STATUS[r.status]})</option>)}
          </Select>
        </div>
        <TextArea label="Printed labels the school uses (one field per line: field: label; other label)" rows={3}
          value={labels} onChange={(e) => setLabels(e.target.value)} placeholder="student_name: Name of student; Learner" />
        <label className="block text-sm text-ink-2">Pages (one PDF, or the page images in order; together under 4 MB)
          <input type="file" accept="application/pdf,image/png,image/jpeg" multiple className="mt-1 block w-full text-sm"
            data-testid="reference-files" onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
        </label>
        {size > MAX_UPLOAD_BYTES && <p className="text-xs text-critical">These files are larger than 4 MB together. Scan at a lower resolution.</p>}
        <Button type="submit" busy={create.isPending} disabled={!school.trim() || files.length === 0 || size > MAX_UPLOAD_BYTES}>Upload</Button>
        <ErrorText error={create.error} />
      </form>
    </Card>
  )
}

function ReferenceDetail({ id }: { id: number }) {
  const detail = useQuery({ queryKey: ['reference', id], queryFn: () => api.reference(id) })
  if (detail.isPending) return <p className="text-xs text-muted">Loading…</p>
  if (detail.error) return <ErrorText error={detail.error} />
  const r = detail.data
  const fields = Object.entries(r.reading?.fields ?? {})
  return (
    <div className="space-y-2 rounded-md bg-surface-2 p-3 text-xs text-ink-2" data-testid="reference-detail">
      <div className="flex flex-wrap gap-2">
        {Array.from({ length: r.pages }, (_, i) => i + 1).map((page) => (
          <a key={page} href={api.referencePageUrl(r.id, page)} target="_blank" rel="noopener">
            <img src={api.referencePageUrl(r.id, page)} alt={`Reference ${r.id}, page ${page}`} loading="lazy"
              className="h-32 w-24 rounded border border-line bg-white object-contain" />
          </a>
        ))}
      </div>
      {r.reading ? (
        <>
          <p>Read as {r.reading.doc_type?.predicted ?? '—'} ({percent(r.reading.doc_type?.confidence)}, {r.reading.doc_type?.model ?? 'model —'}); layout measured on {r.measured_pages} page(s).</p>
          {fields.length > 0 && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3">
              {fields.map(([name, f]) => <div key={name} className="contents"><dt>{name.replaceAll('_', ' ')}</dt><dd className="text-ink">{f?.value ?? '—'}</dd></div>)}
            </dl>
          )}
        </>
      ) : <p>Not read yet.</p>}
      {r.rules && (
        <p>Rules: required {r.rules.required_fields.join(', ') || '—'} · tolerance {r.rules.layout_tolerance}
          {Object.keys(r.rules.field_labels).length > 0 && <span className="block whitespace-pre-wrap">{showLabels(r.rules.field_labels)}</span>}</p>
      )}
      {r.status_reason && <p>Reason ({REFERENCE_STATUS[r.status]}): {r.status_reason} · {r.decided_by} {formatDate(r.decided_at, true)}</p>}
    </div>
  )
}

function Actions({ r }: { r: ApplicantReference }) {
  const qc = useQueryClient()
  const done = () => { qc.invalidateQueries({ queryKey: ['references'] }); qc.invalidateQueries({ queryKey: ['reference', r.id] }) }
  const approve = useMutation({ mutationFn: (reason: string) => api.approveReference(r.id, reason), onSuccess: done })
  const retire = useMutation({ mutationFn: (reason: string) => api.retireReference(r.id, reason), onSuccess: done })
  const retry = useMutation({ mutationFn: (reason: string) => api.retryReference(r.id, reason), onSuccess: done })
  return (
    <div className="flex flex-wrap gap-2">
      {r.status === 'pending' && <ReasonButton label="Approve" variant="primary" busy={approve.isPending} error={approve.error}
        onConfirm={(reason) => approve.mutateAsync(reason)} hint="Check the pages and what was read first." />}
      {(r.status === 'pending' || r.status === 'approved' || r.status === 'failed') && (
        <ReasonButton label="Retire" variant="danger" busy={retire.isPending} error={retire.error} onConfirm={(reason) => retire.mutateAsync(reason)} />
      )}
      {r.status === 'failed' && <ReasonButton label="Read again" busy={retry.isPending} error={retry.error} onConfirm={(reason) => retry.mutateAsync(reason)} />}
    </div>
  )
}

export function References() {
  const [state, setState] = useState<ReferenceStatus | ''>('')
  const [open, setOpen] = useState<number | null>(null)
  const list = useQuery({ queryKey: ['references', state], queryFn: () => api.references(state), refetchInterval: 15_000 })
  const all = useQuery({ queryKey: ['references', ''], queryFn: () => api.references('') })
  const rows = list.data ?? []
  return (
    <Page title="Reference samples">
      <p className="rounded-md border border-line bg-surface p-3 text-sm text-ink-2" data-testid="reference-note">{REFERENCE_NOTE}</p>
      <p className="text-sm text-ink-2">
        An online upload is compared with the latest approved training sample labelled known-good sample of its school and
        type. With samples labelled both known-good and altered, a layout model gives a preliminary placement too; held-out
        samples only evaluate the models.
      </p>
      <UploadReference references={all.data ?? []} />
      <Card title="Samples" actions={
        <select aria-label="Filter by state" value={state} onChange={(e) => setState(e.target.value as ReferenceStatus | '')}
          className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink">
          <option value="">All states</option>
          {(Object.keys(REFERENCE_STATUS) as ReferenceStatus[]).map((s) => <option key={s} value={s}>{REFERENCE_STATUS[s]}</option>)}
        </select>
      }>
        <ErrorText error={list.error} />
        {list.isPending ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No reference samples yet.</Empty> : (
          <Table head={['#', 'School', 'Type', 'Label · use', 'Version', 'State', '']}>
            {rows.map((r) => (
              <tr key={r.id} data-testid="reference-row">
                <Td>{r.id}</Td>
                <Td>{r.issuing_school}</Td>
                <Td>{r.doc_type === 'unknown' ? 'Other' : DOC_TYPE_LABEL[r.doc_type]}</Td>
                <Td>{REFERENCE_LABEL[r.label]}<span className="block text-xs text-muted">{REFERENCE_USE[r.use]}</span></Td>
                <Td>v{r.version}{r.replaces_id && <span className="block text-xs text-muted">replaces #{r.replaces_id}</span>}</Td>
                <Td>{REFERENCE_STATUS[r.status]}{r.error && <span className="block text-xs text-critical">{r.error}</span>}</Td>
                <Td className="space-y-2">
                  <button type="button" className="text-sm text-accent hover:underline" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    {open === r.id ? 'Hide' : 'Pages and reading'}
                  </button>
                  {open === r.id && <ReferenceDetail id={r.id} />}
                  <Actions r={r} />
                </Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  )
}
