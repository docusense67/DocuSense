/** Intake station operator console (backend: app/api/intake_operator.py). No field values here. */
import { request, type IntakeDecision, type IntakeDocType, type IntakeStatus } from '../api'

/** Station state as the console groups the agent's report (ready -> idle, jam -> error, ...). */
export type IntakeStationState = 'idle' | 'scanning' | 'error' | 'offline' | 'no_signal' | 'unknown'
/** Where the page physically is: the gate has not reported yet, the right tray, jammed, wrong tray. */
export type IntakeRouting = 'pending' | 'ok' | 'jam' | 'wrong_tray'

export interface IntakeStation {
  id: number
  name: string
  is_active: boolean
  state: IntakeStationState
  status: string | null
  status_detail: string | null
  last_seen_at: string | null
  intake: boolean // has screened incoming documents (or started a batch)
}

export interface IntakeCounts {
  total: number
  passed_screening: number
  manual_review: number
  tray_1: number
  tray_2: number
  pending: number
  jams: number
  wrong_tray: number
}

export interface IntakeBatchSummary {
  batch_id: number
  started_at: string
  ended_at: string | null
  open: boolean
  counts: IntakeCounts
}

export interface IntakePage {
  id: number
  batch_id: number | null
  sequence: number | null
  doc_type: IntakeDocType
  decision: IntakeDecision
  tray: 1 | 2 // where the page belongs now
  assigned_tray: 1 | 2
  routed_tray: 1 | 2 | null
  routing: IntakeRouting
  status: IntakeStatus
  created_at: string
  reasons: { code: string; text: string }[]
}

export interface IntakeLive {
  station: IntakeStation
  batch: IntakeBatchSummary | null
  documents: IntakePage[] // newest first
  today: IntakeCounts
  checked_at: string
}

export const intakeStationApi = {
  stations: () => request<IntakeStation[]>('/intake-station/stations'),
  live: (stationId: number, limit = 10) =>
    request<IntakeLive>(`/intake-station/stations/${stationId}/live?limit=${limit}`),
  batches: (stationId: number, limit = 5) =>
    request<IntakeBatchSummary[]>(`/intake-station/stations/${stationId}/batches?limit=${limit}`),
  endBatch: (batchId: number) =>
    request<IntakeBatchSummary>(`/intake-station/batches/${batchId}/end`, { method: 'POST' }),
}

// Wording rule: "passed screening" / "needs manual review", never "authentic/genuine/verified".
export const TRAY_LABEL: Record<1 | 2, string> = { 1: 'Passed screening', 2: 'Needs manual review' }

/** What the operator should do with a page, or null when it is where it belongs. */
export function pageAction(p: IntakePage): string | null {
  const n = p.sequence ?? p.id
  if (p.routing === 'jam') return `Jam: take page #${n} out of the gate and put it in Tray 2.`
  if (p.routing === 'wrong_tray') return `Page #${n} went into Tray ${p.routed_tray}: move it to Tray ${p.tray}.`
  return null
}
