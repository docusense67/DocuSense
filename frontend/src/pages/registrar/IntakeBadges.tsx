import type { IntakeDecision, IntakeRow, IntakeStatus } from '../../api'
import { DECISION_LABEL, STATUS_LABEL } from '../../lib/intake'

/* Icon + label, never colour alone (same rule as ResultBadge). */
export function DecisionBadge({ decision, size = 'sm' }: { decision: IntakeDecision; size?: 'sm' | 'lg' }) {
  const passed = decision === 'passed_screening'
  const sizing = size === 'lg' ? 'px-4 py-2 text-lg gap-2' : 'px-2 py-0.5 text-xs gap-1'
  return (
    <span data-testid="intake-decision" data-decision={decision}
      className={`inline-flex items-center rounded-md font-bold ${sizing} ${passed ? 'bg-good text-white' : 'bg-warning text-black'}`}>
      <span aria-hidden>{passed ? '✓' : '!'}</span>{DECISION_LABEL[decision]}
    </span>
  )
}

export function IntakeStatusBadge({ status }: { status: IntakeStatus }) {
  const tone = status === 'accepted' || status === 'data_reviewed' || status === 'linked' ? 'border-good text-good-ink'
    : status === 'rejected' ? 'border-critical text-critical'
      : 'border-line text-ink-2'
  return <span data-testid="intake-status" data-status={status}
    className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>{STATUS_LABEL[status]}</span>
}

/** Assigned tray, and what the gate reported when it differs or jammed. */
export function TrayCell({ row }: { row: Pick<IntakeRow, 'assigned_tray' | 'routed_tray' | 'routing_jam'> }) {
  const issue = row.routing_jam ? 'jammed' : row.routed_tray !== null && row.routed_tray !== row.assigned_tray
    ? `went to Tray ${row.routed_tray}` : null
  return (
    <span>Tray {row.assigned_tray}
      {row.routed_tray === null && !row.routing_jam && <span className="block text-xs text-muted">not routed yet</span>}
      {issue && <span className="block text-xs font-semibold text-critical">! {issue}</span>}
    </span>
  )
}
