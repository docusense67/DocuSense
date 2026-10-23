import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type IntakeDecision, type IntakeDocType, type IntakeFilters, type IntakeSource, type IntakeStatus } from '../../api'
import { Card, Empty, ErrorText, Page, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { DECISION_LABEL, DOC_TYPE_LABEL, STATUS_LABEL, percent } from '../../lib/intake'
import { DecisionBadge, IntakeStatusBadge, TrayCell } from './IntakeBadges'

const STATUSES = Object.keys(STATUS_LABEL) as IntakeStatus[]
const DOC_TYPES = Object.keys(DOC_TYPE_LABEL) as IntakeDocType[]
const DECISIONS = Object.keys(DECISION_LABEL) as IntakeDecision[]
const selectClass = 'rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink'

/** S-09: incoming enrollment documents screened at the intake station, newest first. */
export function IntakeQueue() {
  const [params, setParams] = useSearchParams()
  // Default view: what still needs a decision. "all" shows every status.
  const rawStatus = params.get('status') ?? 'screened'
  const filters: IntakeFilters = {
    status: rawStatus === 'all' ? '' : (rawStatus as IntakeStatus),
    doc_type: (params.get('doc_type') ?? '') as IntakeDocType | '',
    decision: (params.get('decision') ?? '') as IntakeDecision | '',
    batch_id: params.get('batch_id') ?? '',
    source: (params.get('source') ?? '') as IntakeSource | '',
  }
  const queue = useQuery({ queryKey: ['intake', filters], queryFn: () => api.intakeQueue(filters) })
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  const rows = queue.data ?? []

  return (
    <Page title="Incoming documents">
      <p className="text-sm text-ink-2">
        Enrollment documents fed through the intake station. Tray 1 holds pages that passed screening,
        Tray 2 pages that need manual review. Passing screening only means no automatic check flagged the page;
        you make the final decision.
      </p>
      <Card title="Review queue" actions={
        <div className="flex flex-wrap gap-2" data-testid="intake-filters">
          <select aria-label="Filter by status" data-testid="intake-filter-status" value={rawStatus}
            onChange={(e) => setFilter('status', e.target.value === 'screened' ? '' : e.target.value)} className={selectClass}>
            {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
            <option value="all">All statuses</option>
          </select>
          <select aria-label="Filter by screening result" data-testid="intake-filter-decision" value={filters.decision}
            onChange={(e) => setFilter('decision', e.target.value)} className={selectClass}>
            <option value="">All screening results</option>
            {DECISIONS.map((d) => <option key={d} value={d}>{DECISION_LABEL[d]}</option>)}
          </select>
          <select aria-label="Filter by source" data-testid="intake-filter-source" value={filters.source}
            onChange={(e) => setFilter('source', e.target.value)} className={selectClass}>
            <option value="">Station and online</option>
            <option value="station">Intake station</option>
            <option value="online">Online uploads</option>
          </select>
          <select aria-label="Filter by document type" data-testid="intake-filter-doc-type" value={filters.doc_type}
            onChange={(e) => setFilter('doc_type', e.target.value)} className={selectClass}>
            <option value="">All document types</option>
            {DOC_TYPES.map((t) => <option key={t} value={t}>{DOC_TYPE_LABEL[t]}</option>)}
          </select>
          {filters.batch_id && (
            <button type="button" onClick={() => setFilter('batch_id', '')} className="text-sm text-accent hover:underline">
              Batch #{filters.batch_id} ✕
            </button>
          )}
        </div>
      }>
        <ErrorText error={queue.error} />
        {queue.isPending ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No documents match these filters.</Empty> : (
          <div data-testid="intake-queue">
            <Table head={['#', 'Document', 'Screening', 'Tray', 'Received', 'Status']}>
              {rows.map((r) => (
                <tr key={r.id} data-testid="intake-row" data-intake-id={r.id}>
                  <Td>
                    <Link to={`/intake/${r.id}`} className="text-accent hover:underline">#{r.id}</Link>
                    {r.batch_id !== null && (
                      <button type="button" onClick={() => setFilter('batch_id', String(r.batch_id))}
                        className="block text-xs text-muted hover:underline">batch {r.batch_id}{r.sequence !== null ? ` · ${r.sequence}` : ''}</button>
                    )}
                  </Td>
                  <Td>{DOC_TYPE_LABEL[r.doc_type]}
                    {r.doc_type_confidence !== null && <span className="block text-xs text-muted">type confidence {percent(r.doc_type_confidence)}</span>}</Td>
                  <Td><DecisionBadge decision={r.decision} />
                    {r.reasons.length > 0 && <span className="block text-xs text-muted">{r.reasons.length} reason(s)</span>}</Td>
                  <Td><TrayCell row={r} /></Td>
                  <Td>{formatDate(r.created_at, true)}</Td>
                  <Td><IntakeStatusBadge status={r.status} /></Td>
                </tr>
              ))}
            </Table>
          </div>
        )}
      </Card>
    </Page>
  )
}
