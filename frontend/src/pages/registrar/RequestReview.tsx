import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type RequestStatus, type Snapshot, type TimelineEntry } from '../../api'
import { Button, Card, Empty, ErrorText, Field, Page, RequestBadge, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { describe } from '../../lib/audit'
import { CANCELLABLE, CLIENT_LABEL } from '../../lib/status'

const HIDDEN = new Set(['terms', 'document_type'])

/** Flatten a snapshot into comparable rows: scalar fields, then one row per subject grade. */
function rows(snapshot: Snapshot | null): Map<string, string> {
  const out = new Map<string, string>()
  if (!snapshot) return out
  for (const [k, v] of Object.entries(snapshot)) if (!HIDDEN.has(k)) out.set(k.replaceAll('_', ' '), String(v))
  for (const term of snapshot.terms ?? []) {
    out.set(`GWA · ${term.semester} ${term.academic_year}`, term.gwa)
    for (const s of term.subjects) out.set(`${s.subject_code} · ${term.semester} ${term.academic_year}`, `${s.units} u · ${s.grade}`)
  }
  return out
}

function Compare({ printed, current }: { printed: Snapshot | null; current: Snapshot | null }) {
  const a = rows(printed)
  const b = rows(current)
  const keys = [...new Set([...a.keys(), ...b.keys()])]
  return (
    <Table head={['Field', 'On the document', 'Records today']}>
      {keys.map((k) => {
        const same = a.get(k) === b.get(k) || k === 'document number' || k === 'date issued' || k === 'purpose'
        return (
          <tr key={k} className={same ? '' : 'bg-surface-2'}>
            <Td className="text-ink-2">{k}</Td>
            <Td className="num">{a.get(k) ?? '—'}</Td>
            <Td className="num">{b.get(k) ?? (k === 'document number' ? 'assigned at issue' : '—')}{!same && <span className="ml-2 text-xs font-semibold text-critical">≠ differs</span>}</Td>
          </tr>
        )
      })}
    </Table>
  )
}

/** Section 5H: the document's full history, from request to every scan. */
function Timeline({ documentId }: { documentId: number }) {
  const timeline = useQuery({ queryKey: ['timeline', documentId], queryFn: () => api.documentTimeline(documentId) })
  const entries: TimelineEntry[] = timeline.data ?? []
  return (
    <Card title="History (audit trail)">
      {timeline.isPending ? <Empty>Loading…</Empty> : entries.length === 0 ? <Empty>No events yet.</Empty> : (
        <ol className="relative space-y-3 border-l border-line pl-4 text-sm">
          {entries.map((e, i) => (
            <li key={i}>
              <span aria-hidden className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full border border-surface bg-ink-2" />
              <div className="text-ink"><span className="font-medium">{e.action.replaceAll('_', ' ')}</span>
                <span className="text-ink-2"> · {e.user_email ?? 'system'}</span></div>
              {describe(e.details) && <div className="text-xs text-ink-2">{describe(e.details)}</div>}
              <div className="text-xs text-muted">{formatDate(e.timestamp, true)}</div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

export function RequestReview() {
  const id = Number(useParams().id)
  const qc = useQueryClient()
  const detail = useQuery({ queryKey: ['request', id], queryFn: () => api.requestDetail(id) })
  const docId = detail.data?.document?.id
  const link = useQuery({
    queryKey: ['doc-link', docId],
    queryFn: () => api.downloadLink(docId!),
    enabled: !!docId,
    staleTime: 60_000,
  })
  const [reason, setReason] = useState('')
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['request', id] })
    qc.invalidateQueries({ queryKey: ['dashboard'] })
    qc.invalidateQueries({ queryKey: ['requests'] })
    qc.invalidateQueries({ queryKey: ['doc-link'] })
  }
  const act = useMutation({
    mutationFn: (action: 'payment' | 'approve' | 'verify' | 'release' | 'reject' | 'regenerate' | 'revoke' | 'reopen' | 'cancel') => {
      switch (action) {
        case 'payment': return api.confirmPayment(id)
        case 'cancel': return api.cancelRequest(id, reason)
        case 'approve': return api.approve(id)
        case 'verify': return api.verify(id)
        case 'release': return api.release(id)
        case 'reject': return api.reject(id, reason)
        case 'regenerate': return api.regenerate(id, reason)
        case 'revoke': return api.revoke(docId!, reason)
        case 'reopen': return api.reopenDownload(docId!, reason)
      }
    },
    onSuccess: () => { setReason(''); refresh(); qc.invalidateQueries({ queryKey: ['timeline'] }) },
  })

  if (detail.isPending) return <Page title="Request"><Empty>Loading…</Empty></Page>
  if (detail.error) return <Page title="Request"><Empty>{(detail.error as Error).message}</Empty></Page>
  const r = detail.data
  const needsReason = reason.trim().length < 3
  const awaitingPayment = r.status === 'requested' && r.payment_required && !r.payment_confirmed

  return (
    <Page title={`${r.reference_no} · ${r.document_type}`} actions={<Link to="/requests" className="text-sm text-accent hover:underline">← All requests</Link>}>
      <Card>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-1 text-sm">
            <div className="text-ink"><span className="font-semibold">{r.student_name}</span> · {r.student_number}</div>
            <div className="text-ink-2">Purpose: {r.purpose} · {r.copies} copy(ies) · requested {formatDate(r.requested_at, true)}</div>
            <div className="flex items-center gap-2"><RequestBadge status={r.status} />
              {r.document && <span className="num text-ink-2">{r.document.document_number} · {r.document.page_count} page(s)</span>}</div>
            {r.reject_reason && <p className="text-critical">Rejected: {r.reject_reason}</p>}
            {r.processing_note && <p className="text-critical">Generation blocked: {r.processing_note}. Fix the records or template, then retry generation.</p>}
            <p className="text-xs text-muted">Student sees: {CLIENT_LABEL[r.client_status]}</p>
          </div>
          <div className="w-full max-w-sm space-y-2">
            {/* S-11 order: approve the request -> records retrieved, document generated -> verify -> release. */}
            {r.status === 'requested' && (
              <p className="text-sm text-ink-2">
                {awaitingPayment ? 'Waiting for payment. Confirm it, then approve the request. ' : ''}
                Approving starts record retrieval and document generation; you verify the document before release.
              </p>
            )}
            {r.status === 'for_verification' && (
              <p className="text-sm text-ink-2">Check the document against the records below, then verify it or send it back for regeneration.</p>
            )}
            <Field label="Reason (required to reject, regenerate, cancel, revoke or reopen)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="flex flex-wrap gap-2">
              {awaitingPayment && <Button onClick={() => act.mutate('payment')} busy={act.isPending}>Confirm payment</Button>}
              {r.status === 'requested' && <Button onClick={() => act.mutate('approve')} busy={act.isPending} disabled={awaitingPayment}>Approve request</Button>}
              {r.status === 'for_verification' && <Button onClick={() => act.mutate('verify')} busy={act.isPending}>
                {r.auto_release ? 'Verify & release' : 'Verify document'}</Button>}
              {r.status === 'verified' && <Button onClick={() => act.mutate('release')} busy={act.isPending}>Release (student can download)</Button>}
              {CANCELLABLE.includes(r.status) &&
                <Button variant="danger" disabled={needsReason} onClick={() => act.mutate('reject')}>Reject</Button>}
              {(['approved', 'for_verification', 'rejected'] as RequestStatus[]).includes(r.status) &&
                <Button variant="secondary" disabled={needsReason} onClick={() => act.mutate('regenerate')}>
                  {r.status === 'for_verification' ? 'Send back for regeneration' : r.status === 'rejected' ? 'Reopen and regenerate' : 'Retry generation'}</Button>}
              {CANCELLABLE.includes(r.status) &&
                <Button variant="secondary" disabled={needsReason} onClick={() => act.mutate('cancel')}>Cancel request</Button>}
              {['released', 'expired'].includes(r.status) &&
                <Button variant="danger" disabled={needsReason} onClick={() => act.mutate('revoke')}>Revoke document</Button>}
              {r.status === 'expired' &&
                <Button variant="secondary" disabled={needsReason} onClick={() => act.mutate('reopen')}>Reopen download</Button>}
            </div>
            {r.status === 'for_verification' && !r.auto_release && (
              <p className="text-xs text-muted">Release is a separate step at this school (e.g. pickup).</p>
            )}
            <ErrorText error={act.error} />
          </div>
        </div>
      </Card>

      {r.document ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="Document">
            {link.data ? (
              <iframe title="Generated document" src={link.data.url} className="h-[70vh] w-full rounded-md border border-line bg-white" />
            ) : <Empty>{link.error ? (link.error as Error).message : 'Loading preview…'}</Empty>}
          </Card>
          <Card title="Check against records" actions={
            r.differs_from_records ? <span className="text-xs font-semibold text-critical">≠ Records changed since generation</span>
              : <span className="text-xs font-semibold text-good-ink">✓ Matches current records</span>}>
            {r.current_data_error && <p className="mb-2 text-sm text-critical">Records incomplete: {r.current_data_error.join('; ')}</p>}
            <Compare printed={r.snapshot} current={r.current_data} />
          </Card>
        </div>
      ) : <Card><Empty>{r.status === 'requested'
        ? 'No document yet: records are retrieved and the document is generated when you approve the request.'
        : 'No document generated for this request.'}</Empty></Card>}
      {r.document && <Timeline documentId={r.document.id} />}
    </Page>
  )
}
