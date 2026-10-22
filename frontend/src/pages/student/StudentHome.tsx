import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, type ClientStatus, type DocType, type DocRequest } from '../../api'
import {
  Button, Card, ClientBadge, Empty, ErrorText, Field, Page, Select, Table, Td,
} from '../../components/ui'
import { formatDate } from '../../lib/format'
import { CANCELLABLE } from '../../lib/status'

const DOC_LABEL: Record<DocType, string> = { TOR: 'Transcript of Records (TOR)', COE: 'Certificate of Enrollment (COE)' }

// S-11: the registrar approves the request first, then the document is prepared and checked.
const NEXT_STEP: Partial<Record<ClientStatus, string>> = {
  pending: 'Waiting for the registrar to approve it.',
  approved: 'Approved. The registrar is preparing and checking your document.',
  completed: 'Ready to download.',
}

function NewRequest() {
  const qc = useQueryClient()
  const eligibility = useQuery({ queryKey: ['eligibility'], queryFn: api.eligibility })
  const [type, setType] = useState<DocType | ''>('')
  const [purpose, setPurpose] = useState('')
  const [copies, setCopies] = useState(1)
  const submit = useMutation({
    mutationFn: () => api.submitRequest({ document_type: type as DocType, purpose, copies }),
    onSuccess: () => {
      setType(''); setPurpose(''); setCopies(1)
      qc.invalidateQueries({ queryKey: ['my-requests'] })
      qc.invalidateQueries({ queryKey: ['eligibility'] })
    },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); submit.mutate() }
  const types = Object.keys(DOC_LABEL) as DocType[]

  return (
    <Card title="Request a document">
      <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-[2fr_2fr_1fr_auto] sm:items-end">
        <Select label="Document" required value={type} onChange={(e) => setType(e.target.value as DocType)}>
          <option value="">Choose…</option>
          {types.map((t) => {
            const reasons = eligibility.data?.[t] ?? []
            return <option key={t} value={t} disabled={reasons.length > 0}>{DOC_LABEL[t]}{reasons.length ? ' (not available)' : ''}</option>
          })}
        </Select>
        <Field label="Purpose" required minLength={2} placeholder="e.g. Employment" value={purpose}
          onChange={(e) => setPurpose(e.target.value)} />
        <Field label="Copies" type="number" min={1} max={10} value={copies}
          onChange={(e) => setCopies(Number(e.target.value))} />
        <Button type="submit" busy={submit.isPending} disabled={!type}>Submit</Button>
      </form>
      {types.filter((t) => (eligibility.data?.[t] ?? []).length > 0).map((t) => (
        <p key={t} className="mt-2 text-xs text-ink-2">
          <span className="font-medium">{t} not available:</span> {eligibility.data![t].join('; ')}
        </p>
      ))}
      {submit.data?.status === 'rejected' && (
        <p className="mt-2 text-sm text-critical">Request {submit.data.reference_no} was not accepted: {submit.data.reject_reason}</p>
      )}
      {submit.data && submit.data.status !== 'rejected' && (
        <p className="mt-2 text-sm text-good-ink">✓ Request {submit.data.reference_no} submitted. {NEXT_STEP[submit.data.client_status] ?? ''}</p>
      )}
      <ErrorText error={submit.error} />
    </Card>
  )
}

function Download({ request }: { request: DocRequest }) {
  const download = useMutation({
    mutationFn: () => api.downloadLink(request.document!.id),
    onSuccess: ({ url }) => window.open(url, '_blank', 'noopener'),
  })
  return (
    <span className="flex flex-col items-start gap-1">
      <Button variant="secondary" onClick={() => download.mutate()} busy={download.isPending}>Download PDF</Button>
      <ErrorText error={download.error} />
    </span>
  )
}

function MyRequests() {
  const qc = useQueryClient()
  const requests = useQuery({ queryKey: ['my-requests'], queryFn: () => api.requests() })
  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelRequest(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-requests'] }),
  })
  if (requests.isPending) return <Card title="My requests"><Empty>Loading…</Empty></Card>
  const rows = requests.data ?? []
  return (
    <Card title="My requests">
      {rows.length === 0 ? <Empty>No requests yet.</Empty> : (
        <Table head={['Request', 'Status']}>
          {rows.map((r) => (
            <tr key={r.id}>
              <Td>
                <span className="font-medium">{r.document_type}</span> · {r.purpose}
                <span className="num block whitespace-nowrap text-xs text-muted">{r.reference_no} · {formatDate(r.requested_at)}</span>
              </Td>
              {/* Actions live with the status so they stay visible on phone screens. */}
              <Td>
                <ClientBadge status={r.client_status} />
                {NEXT_STEP[r.client_status] && r.client_status !== 'completed' && (
                  <span className="mt-1 block text-xs text-muted">{NEXT_STEP[r.client_status]}</span>
                )}
                {r.reject_reason && <span className="mt-1 block text-xs text-critical">{r.reject_reason}</span>}
                {r.document?.can_download && (
                  <span className="mt-1 block text-xs text-muted">Download until {formatDate(r.document.download_expires_at)}</span>
                )}
                {r.status === 'expired' && <span className="mt-1 block text-xs text-muted">Download window closed; ask the registrar to reopen it. The document is still valid.</span>}
                {(r.document?.downloads.length ?? 0) > 0 && (
                  <span className="mt-1 block text-xs text-muted">Downloaded {r.document!.downloads.length} time(s), last {formatDate(r.document!.downloads.at(-1), true)}</span>
                )}
                <div className="mt-2 flex flex-wrap gap-2">
                  {r.document?.can_download && <Download request={r} />}
                  {CANCELLABLE.includes(r.status) && (
                    <Button variant="secondary" onClick={() => cancel.mutate(r.id)} busy={cancel.isPending && cancel.variables === r.id}>Cancel</Button>
                  )}
                </div>
              </Td>
            </tr>
          ))}
        </Table>
      )}
      <ErrorText error={cancel.error} />
    </Card>
  )
}

function MyRecords() {
  const records = useQuery({ queryKey: ['my-records'], queryFn: api.myRecords })
  return (
    <Card title="My records">
      <p className="mb-3 text-xs text-muted">Check these before requesting. Report errors to the registrar.</p>
      {(records.data ?? []).length === 0 ? <Empty>No grades on record yet.</Empty> : records.data!.map((term) => (
        <div key={term.term_id} className="mb-4">
          <div className="mb-1 flex items-baseline justify-between text-sm">
            <span className="font-medium text-ink">{term.semester} Semester, AY {term.academic_year}</span>
            <span className="text-ink-2">GWA <span className="num font-semibold text-ink">{term.gwa ?? '—'}</span>
              {!term.is_grades_locked && <span className="ml-2 text-xs text-muted">(grades not final)</span>}</span>
          </div>
          <Table head={['Code', 'Subject', 'Units', 'Grade', 'Remarks']}>
            {term.subjects.map((s) => (
              <tr key={s.subject_code}>
                <Td>{s.subject_code}</Td><Td>{s.subject_title}</Td>
                <Td className="num">{Number(s.units)}</Td><Td className="num">{s.grade ?? '—'}</Td><Td>{s.remarks ?? ''}</Td>
              </tr>
            ))}
          </Table>
        </div>
      ))}
    </Card>
  )
}

export function StudentHome() {
  return (
    <Page title="My documents">
      <NewRequest />
      <MyRequests />
      <MyRecords />
    </Page>
  )
}
