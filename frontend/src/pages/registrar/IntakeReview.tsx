import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type IntakeAction, type IntakeDetail } from '../../api'
import { Button, Card, Empty, ErrorText, Field, Page, Table, Td } from '../../components/ui'
import { describe } from '../../lib/audit'
import { formatDate } from '../../lib/format'
import { DOC_TYPE_LABEL, QUALITY_ISSUE_LABEL, indicatorLabel, percent } from '../../lib/intake'
import { DecisionBadge, IntakeStatusBadge, TrayCell } from './IntakeBadges'
import { StudentData } from './IntakeStudentData'

const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function Measurements({ d }: { d: IntakeDetail }) {
  const checks = d.checks ?? {}
  const quality = d.quality ?? {}
  const issues = Array.isArray(quality.issues) ? (quality.issues as unknown[]).map(String) : []
  const headingsFound = num(checks.expected_headings_found)
  const headingsTotal = num(checks.expected_headings_total)
  const fieldsFound = num(checks.expected_fields_found)
  const fieldsTotal = num(checks.expected_fields_total)
  const rows: [string, string][] = [
    ['Photo quality', quality.ok === true ? 'usable' : `not usable${issues.length ? ` (${issues.map((i) => QUALITY_ISSUE_LABEL[i] ?? i).join(', ')})` : ''}`],
    ['Document type (station)', `${d.predicted_doc_type ?? '—'} · ${percent(d.doc_type_confidence)} confident`],
    ['Headings found', headingsFound !== null && headingsTotal !== null ? `${headingsFound} of ${headingsTotal}` : '—'],
    ['Fields found', fieldsFound !== null && fieldsTotal !== null ? `${fieldsFound} of ${fieldsTotal}` : '—'],
    ['Layout match', percent(checks.layout_score)],
    ['Seal / logo match', checks.seal_logo_score === null || checks.seal_logo_score === undefined ? 'not measured' : percent(checks.seal_logo_score)],
    ['Font consistency', percent(checks.font_consistency)],
    ['Alignment', percent(checks.alignment_score)],
    ['Similarity to reference samples', d.similarity && num(d.similarity.score) !== null
      ? `${percent(d.similarity.score)}${num(d.similarity.reference_count) !== null ? ` (${d.similarity.reference_count} samples)` : ''}` : 'not measured'],
  ]
  return (
    <Card title="What the station measured">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid="intake-measurements">
        {rows.map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-ink-2">{k}</dt><dd className="num text-ink">{v}</dd></div>
        ))}
      </dl>
    </Card>
  )
}

function Indicators({ d }: { d: IntakeDetail }) {
  return (
    <Card title="Suspicious features flagged by the station">
      {d.indicators.length === 0 ? <Empty>None flagged.</Empty> : (
        <ul className="space-y-1 text-sm" data-testid="intake-indicators">
          {[...d.indicators].sort((a, b) => (b.severity ?? 0) - (a.severity ?? 0)).map((ind, i) => (
            <li key={i} data-testid="intake-indicator" data-code={ind.code}>
              <span className={(ind.severity ?? 0) >= 0.5 ? 'font-semibold text-ink' : 'text-ink-2'}>
                <span aria-hidden>{(ind.severity ?? 0) >= 0.5 ? '! ' : '· '}</span>{indicatorLabel(ind.code)}
              </span>
              <span className="text-xs text-muted"> · severity {percent(ind.severity)}{ind.detail ? ` · ${ind.detail}` : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ExtractedFields({ d }: { d: IntakeDetail }) {
  const entries = Object.entries(d.fields ?? {})
  if (entries.length === 0) return null
  return (
    <Card title="Raw text read by the station (kept as read)">
      <Table head={['Field', 'Read as', 'Confidence']}>
        {entries.map(([name, f]) => (
          <tr key={name}>
            <Td className="text-ink-2">{name.replaceAll('_', ' ')}</Td>
            <Td className="num">{f && f.value !== undefined && f.value !== null ? String(f.value) : '—'}</Td>
            <Td>{percent(f?.confidence)}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  )
}

const ACTIONS: { action: IntakeAction; label: string; variant: 'primary' | 'secondary' | 'danger'; testid: string }[] = [
  { action: 'accept', label: 'Accept', variant: 'primary', testid: 'intake-accept' },
  { action: 'request_resubmission', label: 'Request resubmission', variant: 'secondary', testid: 'intake-request-resubmission' },
  { action: 'reject', label: 'Reject', variant: 'danger', testid: 'intake-reject' },
]

function Review({ d }: { d: IntakeDetail }) {
  const qc = useQueryClient()
  const [reason, setReason] = useState('')
  const review = useMutation({
    mutationFn: (action: IntakeAction) => api.reviewIntake(d.id, action, reason.trim() || undefined),
    onSuccess: () => {
      setReason('')
      qc.invalidateQueries({ queryKey: ['intake'] })
      qc.invalidateQueries({ queryKey: ['intake-detail', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-fields', d.id] })
    },
  })
  if (d.status !== 'screened') {
    return (
      <p className="text-sm text-ink-2" data-testid="intake-reviewed">
        <IntakeStatusBadge status={d.status === 'data_reviewed' || d.status === 'linked' ? 'accepted' : d.status} /> by {d.review.reviewed_by ?? '—'}, {formatDate(d.review.reviewed_at, true)}
        {d.review.reason && <span className="block">Reason: {d.review.reason}</span>}
      </p>
    )
  }
  const hasReason = reason.trim().length >= 3
  // Reason required to reject, to request a resubmission, and to accept a flagged document.
  const needsReason = (a: IntakeAction) => a !== 'accept' || d.decision === 'manual_review'
  return (
    <div className="space-y-2 rounded-md border border-line p-3" data-testid="intake-review">
      <p className="text-sm font-semibold text-ink">Your decision</p>
      <Field label={d.decision === 'manual_review'
        ? 'Reason (required; kept in the audit trail)'
        : 'Reason (required to reject or request a resubmission)'}
        value={reason} onChange={(e) => setReason(e.target.value)} data-testid="intake-reason" />
      <div className="flex flex-wrap gap-2">
        {ACTIONS.map((a) => (
          <Button key={a.action} variant={a.variant} data-testid={a.testid} busy={review.isPending && review.variables === a.action}
            disabled={review.isPending || (needsReason(a.action) && !hasReason)} onClick={() => review.mutate(a.action)}>
            {a.label}
          </Button>
        ))}
      </div>
      <ErrorText error={review.error} />
    </div>
  )
}

export function IntakeReview() {
  const id = Number(useParams().id)
  const detail = useQuery({ queryKey: ['intake-detail', id], queryFn: () => api.intakeDetail(id) })
  const back = <Link to="/intake" className="text-sm text-accent hover:underline">← Incoming documents</Link>
  if (detail.isPending) return <Page title="Incoming document" actions={back}><Empty>Loading…</Empty></Page>
  if (detail.error) return <Page title="Incoming document" actions={back}><Empty>{(detail.error as Error).message}</Empty></Page>
  const d = detail.data

  return (
    <Page title={`#${d.id} · ${DOC_TYPE_LABEL[d.doc_type]}`} actions={back}>
      <Card>
        <div className="flex flex-wrap items-start gap-5" data-testid="intake-detail">
          <DecisionBadge decision={d.decision} size="lg" />
          <div className="min-w-0 flex-1 space-y-1 text-sm">
            <div className="flex flex-wrap items-center gap-2 text-ink-2">
              <IntakeStatusBadge status={d.status} />
              <span>{d.station ?? 'station'} · {formatDate(d.created_at, true)}</span>
              {d.batch_id !== null && <span>· batch {d.batch_id}{d.sequence !== null ? `, page ${d.sequence}` : ''}</span>}
              {d.student && <span>· student <Link to={`/records/students/${d.student.id}`} className="text-accent hover:underline">{d.student.name} ({d.student.student_number})</Link></span>}
              <span>· <TrayCell row={d} /></span>
            </div>
            {d.reasons_explained.length === 0
              ? <p className="text-ink">No automatic check flagged this document.</p>
              : (
                <ul className="list-inside list-disc text-ink" data-testid="intake-reasons">
                  {d.reasons_explained.map((r) => <li key={r.code} data-testid="intake-reason-item" data-code={r.code}>{r.text}</li>)}
                </ul>
              )}
          </div>
        </div>
        <div className="mt-4"><Review d={d} /></div>
        <p className="mt-3 border-t border-line pt-2 text-xs text-muted">{d.note}</p>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title={d.submission ? `Uploaded pages (${d.submission.pages})` : 'Page image'}>
          {d.submission ? (
            <div className="space-y-3" data-testid="intake-online-pages">
              <p className="text-sm text-ink-2">
                Uploaded online by {d.submission.applicant_email ?? 'an applicant'} as {DOC_TYPE_LABEL[d.submission.declared_type]}{' '}
                from {d.submission.issuing_school}.
              </p>
              {Array.from({ length: d.submission.pages }, (_, i) => i + 1).map((page) => (
                <a key={page} href={api.applicantPageUrl(d.submission!.id, page)} target="_blank" rel="noopener">
                  <img src={api.applicantPageUrl(d.submission!.id, page)} alt={`Uploaded page ${page} of incoming document ${d.id}`}
                    loading="lazy" className="mb-2 w-full rounded-md border border-line bg-white" />
                </a>
              ))}
            </div>
          ) : d.has_image ? (
            <a href={api.intakeImageUrl(d.id)} target="_blank" rel="noopener">
              <img src={api.intakeImageUrl(d.id)} alt={`Captured page of incoming document ${d.id}`}
                data-testid="intake-image" className="w-full rounded-md border border-line bg-white" />
            </a>
          ) : <Empty>No image stored.</Empty>}
        </Card>
        <div className="space-y-5">
          <Indicators d={d} />
          <Measurements d={d} />
        </div>
      </div>

      <StudentData d={d} />

      <ExtractedFields d={d} />

      <Card title="History (audit trail)">
        <ol className="space-y-2 text-sm" data-testid="intake-history">
          {d.history.map((e, i) => (
            <li key={i}>
              <span className="font-medium text-ink">{e.action.replaceAll('_', ' ')}</span>
              <span className="text-ink-2"> · {e.user_email ?? 'station'} · {formatDate(e.timestamp, true)}</span>
              {describe(e.details) && <div className="text-xs text-ink-2">{describe(e.details)}</div>}
            </li>
          ))}
        </ol>
      </Card>
    </Page>
  )
}
