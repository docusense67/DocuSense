import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type QueueItem } from '../../api'
import { DocsChart } from '../../components/DocsChart'
import { Card, Empty, Page, Stat, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'

const PERIODS = [
  { key: 'today', label: 'Today' },
  { key: 'week', label: 'Last 7 days' },
  { key: 'month', label: 'Last 30 days' },
  { key: 'range', label: 'Date range' },
] as const

const dateInput = 'rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink'

/** One of the two S-11 work queues: requests to approve, generated documents to verify. */
function Queue({ title, items, link, empty, readOnly }: { title: string; items: QueueItem[]; link: string; empty: string; readOnly: boolean }) {
  return (
    <Card title={title} actions={!readOnly && <Link to={link} className="text-xs text-accent hover:underline">All</Link>}>
      {items.length === 0 ? <Empty>{empty}</Empty> : (
        <Table head={['Reference', 'Student', 'Type', 'Requested']}>
          {items.map((q) => (
            <tr key={q.id}>
              <Td>{readOnly ? q.reference_no : <Link className="text-accent hover:underline" to={`/requests/${q.id}`}>{q.reference_no}</Link>}</Td>
              <Td>{q.student_name}</Td><Td>{q.document_type}</Td><Td>{formatDate(q.requested_at)}</Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}

export function RegistrarHome({ readOnly = false }: { readOnly?: boolean }) {
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [period, setPeriod] = useState<(typeof PERIODS)[number]['key']>('today')
  const [range, setRange] = useState({ date_from: '', date_to: '' })
  const ranged = period === 'range' && (range.date_from || range.date_to)
  const dash = useQuery({
    queryKey: ['dashboard', ranged ? range : null],
    queryFn: () => api.dashboard(ranged ? range : {}),
    refetchInterval: 30_000,
  })
  if (dash.isPending) return <Page title="Dashboard"><Empty>Loading…</Empty></Page>
  if (dash.error) return <Page title="Dashboard"><Empty>{(dash.error as Error).message}</Empty></Page>
  const d = dash.data
  const cards = d.requests[period] ?? { total: 0, pending: 0, approved: 0, released: 0, rejected: 0 }
  // Scan details need the operator permission; others see the queue without links.
  const canOpenScans = !readOnly && !!me.data?.can_validate

  return (
    <Page title={readOnly ? 'Overview (read-only)' : 'Registrar dashboard'}>
      <Card title="Requests" actions={
        <div className="flex gap-1" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p.key} onClick={() => setPeriod(p.key)}
              className={`rounded-md px-2 py-1 text-xs ${period === p.key ? 'bg-surface-2 font-semibold text-ink' : 'text-ink-2'}`}>
              {p.label}
            </button>
          ))}
        </div>
      }>
        {period === 'range' && (
          <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-ink-2">
            <label>From <input type="date" className={dateInput} value={range.date_from} onChange={(e) => setRange({ ...range, date_from: e.target.value })} /></label>
            <label>To <input type="date" className={dateInput} value={range.date_to} onChange={(e) => setRange({ ...range, date_to: e.target.value })} /></label>
            <span className="text-muted">Also applies to the validation summary.</span>
          </div>
        )}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          <Stat label="Total" value={cards.total} />
          <Stat label="Pending" value={cards.pending} />
          <Stat label="Approved" value={cards.approved} />
          <Stat label="Completed" value={cards.released} tone="good" />
          <Stat label="Rejected" value={cards.rejected} tone="critical" />
        </div>
        {d.blocked_requests > 0 && (
          <p className="mt-3 text-sm text-critical">
            ! {d.blocked_requests} request(s) blocked during generation (missing data or no active template).{' '}
            {!readOnly && <Link to="/requests?status=approved" className="underline">Review</Link>}
          </p>
        )}
      </Card>

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Queue title="Approval queue" items={d.approval_queue} link="/requests?status=requested"
          empty="No requests waiting for approval." readOnly={readOnly} />
        <Queue title="Verification queue" items={d.verification_queue} link="/requests?status=for_verification"
          empty="No generated documents waiting for verification." readOnly={readOnly} />
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Card title="Documents generated per month">
          <DocsChart data={d.documents_by_month} />
        </Card>
        <Card title="Validation summary">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Scanned" value={d.validation.total} />
            <Stat label="✓ Valid" value={d.validation.VALID} tone="good" />
            <Stat label="✕ Invalid" value={d.validation.INVALID} tone="critical" />
            <Stat label="! Needs review" value={d.validation.NEEDS_REVIEW} tone="warning" />
          </div>
        </Card>
        <Card title={`Needs review (${d.open_reviews} open)`}>
          {d.review_queue.length === 0 ? <Empty>No scans waiting for review.</Empty> : (
            <Table head={['Document', 'Reason', 'Scanned']}>
              {d.review_queue.map((r) => (
                <tr key={r.scanned_document_id}>
                  <Td>{canOpenScans ? <Link className="text-accent hover:underline" to={`/scan/${r.scanned_document_id}`}>{r.document_number ?? '?'}</Link> : (r.document_number ?? '?')}</Td>
                  <Td className="text-ink-2">{r.reason}</Td><Td>{formatDate(r.scanned_at, true)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Card title="Data health">
          <p className="mb-2 text-xs text-muted">Students whose records will block a TOR request.</p>
          {d.data_health.length === 0 ? <Empty>No problems found.</Empty> : (
            <Table head={['Student', 'Issues']}>
              {d.data_health.map((h) => (
                <tr key={h.student_id}><Td>{h.name}<span className="block text-xs text-muted">{h.student_number}</span></Td>
                  <Td className="text-ink-2">{h.issues.join('; ')}</Td></tr>
              ))}
            </Table>
          )}
        </Card>
        <Card title="Recent activity">
          <ul className="divide-y divide-line text-sm">
            {d.recent_activity.map((a, i) => (
              <li key={i} className="flex justify-between gap-3 py-1.5">
                <span className="text-ink">{a.action.replaceAll('_', ' ')}<span className="text-muted"> · {a.user ?? 'system'}</span></span>
                <span className="shrink-0 text-xs text-muted">{formatDate(a.timestamp, true)}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </Page>
  )
}
