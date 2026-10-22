import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type DocType, type RequestStatus } from '../../api'
import {
  Button, Card, Empty, ErrorText, Field, Page, RequestBadge, Select, Table, Td,
} from '../../components/ui'
import { formatDate } from '../../lib/format'
import { REQUEST_LABEL, REQUEST_STATUSES } from '../../lib/status'

function WalkIn() {
  const qc = useQueryClient()
  const [studentId, setStudentId] = useState('')
  const [type, setType] = useState<DocType>('COE')
  const [purpose, setPurpose] = useState('')
  const students = useQuery({ queryKey: ['students'], queryFn: () => api.students() })
  const submit = useMutation({
    mutationFn: () => api.submitRequest({ document_type: type, purpose, copies: 1, student_id: Number(studentId) }),
    onSuccess: () => { setPurpose(''); qc.invalidateQueries({ queryKey: ['requests'] }) },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); submit.mutate() }
  return (
    <Card title="Walk-in request">
      <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-[2fr_1fr_2fr_auto] sm:items-end">
        <Select label="Student" required value={studentId} onChange={(e) => setStudentId(e.target.value)}>
          <option value="">Choose…</option>
          {(students.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.last_name}, {s.first_name} ({s.student_number})</option>)}
        </Select>
        <Select label="Document" value={type} onChange={(e) => setType(e.target.value as DocType)}>
          <option value="TOR">TOR</option><option value="COE">COE</option>
        </Select>
        <Field label="Purpose" required minLength={2} value={purpose} onChange={(e) => setPurpose(e.target.value)} />
        <Button type="submit" busy={submit.isPending}>File request</Button>
      </form>
      {submit.data && <p className="mt-2 text-sm text-ink-2">{submit.data.reference_no}: {REQUEST_LABEL[submit.data.status]}{submit.data.reject_reason ? ` (${submit.data.reject_reason})` : ''}</p>}
      <ErrorText error={submit.error} />
    </Card>
  )
}

export function RequestList() {
  const [params, setParams] = useSearchParams()
  const status = (params.get('status') || undefined) as RequestStatus | undefined
  const requests = useQuery({ queryKey: ['requests', status], queryFn: () => api.requests({ status }) })

  return (
    <Page title="Requests">
      <WalkIn />
      <Card title="All requests" actions={
        <select aria-label="Filter by status" value={status ?? ''} onChange={(e) => setParams(e.target.value ? { status: e.target.value } : {})}
          className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink">
          <option value="">All statuses</option>
          {REQUEST_STATUSES.map((s) => <option key={s} value={s}>{REQUEST_LABEL[s]}</option>)}
        </select>
      }>
        {requests.isPending ? <Empty>Loading…</Empty> : (requests.data ?? []).length === 0 ? <Empty>No requests.</Empty> : (
          <Table head={['Reference', 'Student', 'Document', 'Requested', 'Status']}>
            {requests.data!.map((r) => (
              <tr key={r.id}>
                <Td><Link to={`/requests/${r.id}`} className="text-accent hover:underline">{r.reference_no}</Link></Td>
                <Td>{r.student_name}<span className="block text-xs text-muted">{r.student_number}</span></Td>
                <Td>{r.document_type}{r.document && <span className="block text-xs text-muted">{r.document.document_number}</span>}</Td>
                <Td>{formatDate(r.requested_at)}</Td>
                <Td><RequestBadge status={r.status} />
                  {r.processing_note && <span className="mt-1 block text-xs text-critical">Blocked: {r.processing_note}</span>}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  )
}
