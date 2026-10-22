import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, Card, Empty, ErrorText } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { DOC_TYPE_LABEL } from '../../lib/intake'
import {
  intakeStationApi, pageAction, TRAY_LABEL,
  type IntakeCounts, type IntakeLive, type IntakePage, type IntakeStation,
} from '../../lib/intakeStation'

/*
 * Incoming documents station (operator console). Designed for the station's TFT LCD first
 * (TFT_WIDTH x TFT_HEIGHT, default 800x480): one poll, the latest page's tray as the largest
 * thing on screen, counts next to it, the last pages below. Never shows extracted field values
 * or page images: reviewing incoming documents stays with registrar staff.
 * Wording: "passed screening" / "needs manual review" only.
 */

const STATION_KEY = 'adgvs.intakeStation'
function remember(value: number) {
  try { localStorage.setItem(STATION_KEY, String(value)) } catch { /* storage blocked */ }
}
function recall(): number | null {
  try { return Number(localStorage.getItem(STATION_KEY)) || null } catch { return null }
}

const ERROR_TEXT: Record<string, string> = {
  jam: 'Paper jam: clear the feeder and re-feed the page',
  double_feed: 'Double feed: take the pages out and re-feed them one at a time',
}

/** Station status banner: icon + words, never colour alone. */
function StatusBanner({ station }: { station: IntakeStation }) {
  const detail = station.status_detail ? ` · ${station.status_detail}` : ''
  let tone = 'border-line bg-surface text-ink'
  let text: string
  switch (station.state) {
    case 'idle': text = '● Online · ready for the next stack'; break
    case 'scanning': text = '● Online · screening pages'; break
    case 'error':
      tone = 'border-critical bg-critical text-white'
      text = `! ${ERROR_TEXT[station.status ?? ''] ?? 'Station problem'}${detail}`
      break
    case 'offline':
      tone = 'border-critical bg-critical text-white'
      text = `✕ Station offline${detail}`
      break
    case 'no_signal':
      tone = 'border-warning bg-warning text-black'
      text = `○ No signal from the station since ${formatDate(station.last_seen_at, true)}: check that it is switched on`
      break
    default: text = '○ No status reported yet'
  }
  const showDetail = station.state === 'idle' || station.state === 'scanning'
  return (
    <p data-testid="intake-station-status" data-state={station.state} role="status"
      className={`rounded-lg border px-3 py-2 text-base font-semibold ${tone}`}>
      {text}{showDetail && detail && <span className="font-normal text-ink-2">{detail}</span>}
    </p>
  )
}

const TRAY_TONE: Record<1 | 2, string> = {
  1: 'bg-good text-white', // green: passed screening
  2: 'bg-warning text-black', // amber: needs manual review
}
const TRAY_ICON: Record<1 | 2, string> = { 1: '✓', 2: '!' }

/** The big tray indicator for the latest page: readable at arm's length. */
function LatestPage({ page, batchOpen }: { page: IntakePage | undefined; batchOpen: boolean }) {
  if (!page) {
    return (
      <div data-testid="intake-latest" className="flex min-h-36 items-center justify-center rounded-xl border border-dashed border-line p-4 text-center text-lg text-ink-2">
        {batchOpen ? 'Waiting for the first page of this batch…' : 'Place a stack in the feeder to start.'}
      </div>
    )
  }
  const action = pageAction(page)
  const where = page.routing === 'pending' ? `Going to Tray ${page.tray}` : `Tray ${page.tray}`
  return (
    <div data-testid="intake-latest" data-tray={page.tray} data-routing={page.routing}
      className={`rounded-xl p-4 ${TRAY_TONE[page.tray]}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span data-testid="intake-latest-tray" className="text-5xl font-extrabold leading-tight">
          <span aria-hidden>{TRAY_ICON[page.tray]} </span>{where}
        </span>
        <span className="num text-xl font-semibold">Page #{page.sequence ?? '—'}</span>
      </div>
      <div className="mt-1 text-2xl font-bold">{TRAY_LABEL[page.tray]}</div>
      <div className="mt-1 text-lg">{DOC_TYPE_LABEL[page.doc_type] ?? page.doc_type}</div>
      {action && (
        <p data-testid="intake-latest-action" role="alert" className="mt-2 rounded-md bg-critical px-2 py-1 text-lg font-bold text-white">
          {action}
        </p>
      )}
      {page.reasons.length > 0 && (
        <ul className="mt-2 list-inside list-disc text-base">
          {page.reasons.slice(0, 2).map((r) => <li key={r.code}>{r.text}</li>)}
          {page.reasons.length > 2 && <li>{page.reasons.length - 2} more reason(s)</li>}
        </ul>
      )}
    </div>
  )
}

function TrayCount({ tray, value }: { tray: 1 | 2; value: number }) {
  return (
    <div data-testid={`intake-count-tray${tray}`} className="rounded-lg border border-line bg-surface px-3 py-2">
      <div className="flex items-center gap-1.5 text-sm font-medium text-ink-2">
        <span aria-hidden className={`inline-block h-3 w-3 rounded-full ${tray === 1 ? 'bg-good' : 'bg-warning'}`} />
        Tray {tray} · {TRAY_LABEL[tray]}
      </div>
      <div className="num text-4xl font-bold text-ink">{value}</div>
    </div>
  )
}

function Counts({ live }: { live: IntakeLive }) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const batch = live.batch
  const c: IntakeCounts = batch?.open ? batch.counts : live.today
  const end = useMutation({
    mutationFn: () => intakeStationApi.endBatch(batch!.batch_id),
    onSuccess: () => { setConfirming(false); qc.invalidateQueries({ queryKey: ['intake-live'] }) },
  })
  const issues = [
    c.pending > 0 && `${c.pending} waiting for the gate`,
    c.jams > 0 && `${c.jams} jammed`,
    c.wrong_tray > 0 && `${c.wrong_tray} in the wrong tray`,
  ].filter(Boolean).join(' · ')
  return (
    <div className="space-y-2">
      <div data-testid="intake-batch-heading" className="text-sm font-semibold text-ink">
        {batch?.open ? `Batch ${batch.batch_id} · running since ${formatDate(batch.started_at, true)}`
          : batch ? `Today · last batch ${batch.batch_id} ended` : 'Today'}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <TrayCount tray={1} value={c.tray_1} />
        <TrayCount tray={2} value={c.tray_2} />
      </div>
      <div className="text-base text-ink">
        <span data-testid="intake-count-total" className="font-semibold">{c.total} page{c.total === 1 ? '' : 's'}</span>
        {issues && <span className="text-ink-2"> · {issues}</span>}
      </div>
      {batch?.open && (confirming ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-ink">End batch {batch.batch_id} now?</span>
          <Button data-testid="intake-end-batch-confirm" variant="danger" onClick={() => end.mutate()} busy={end.isPending}>Yes, end batch</Button>
          <Button variant="secondary" onClick={() => setConfirming(false)}>Cancel</Button>
        </div>
      ) : (
        <Button data-testid="intake-end-batch" variant="secondary" className="py-2 text-base" onClick={() => setConfirming(true)}>End batch</Button>
      ))}
      <ErrorText error={end.error} />
    </div>
  )
}

const time = (value: string) => {
  const d = new Date(value.endsWith('Z') || value.includes('+') ? value : value + 'Z')
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function PageList({ pages }: { pages: IntakePage[] }) {
  if (pages.length === 0) return <Empty>No pages yet.</Empty>
  return (
    <ul data-testid="intake-doc-list" className="divide-y divide-line">
      {pages.map((p) => {
        const action = pageAction(p)
        return (
          <li key={p.id} data-testid="intake-doc-row" data-tray={p.tray} data-decision={p.decision}
            className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2">
            <span className="num w-10 shrink-0 text-lg font-semibold text-ink">#{p.sequence ?? '—'}</span>
            <span className={`inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-0.5 text-sm font-bold ${TRAY_TONE[p.tray]}`}>
              <span aria-hidden>{TRAY_ICON[p.tray]}</span>Tray {p.tray} · {TRAY_LABEL[p.tray]}
            </span>
            <span className="min-w-0 flex-1 basis-48 text-base text-ink">
              {DOC_TYPE_LABEL[p.doc_type] ?? p.doc_type}
              {p.routing === 'pending' && <span className="text-ink-2"> · going to the tray</span>}
              {action && <span className="block font-semibold text-critical">{action}</span>}
              {p.reasons.length > 0 && (
                <span className="block text-sm text-ink-2">{p.reasons.map((r) => r.text).join(' ')}</span>
              )}
            </span>
            <span className="num shrink-0 text-sm text-muted">{time(p.created_at)}</span>
          </li>
        )
      })}
    </ul>
  )
}

function RecentBatches({ stationId }: { stationId: number }) {
  const batches = useQuery({ queryKey: ['intake-batches', stationId], queryFn: () => intakeStationApi.batches(stationId), refetchInterval: 10_000 })
  if (!batches.data?.length) return null
  return (
    <Card title="Recent batches">
      <ul className="divide-y divide-line text-sm">
        {batches.data.map((b) => (
          <li key={b.batch_id} className="flex flex-wrap gap-x-3 py-1.5 text-ink">
            <span className="font-semibold">Batch {b.batch_id}{b.open && ' (running)'}</span>
            <span className="text-ink-2">{formatDate(b.started_at, true)}</span>
            <span className="num">{b.counts.total} pages · Tray 1: {b.counts.tray_1} · Tray 2: {b.counts.tray_2}</span>
            {b.counts.jams + b.counts.wrong_tray > 0 && (
              <span className="font-semibold text-critical">! {b.counts.jams + b.counts.wrong_tray} routing issue(s)</span>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function IntakeStationPage() {
  const [chosen, setChosen] = useState<number | null>(recall)
  const stations = useQuery({ queryKey: ['intake-stations'], queryFn: intakeStationApi.stations, refetchInterval: 5000 })
  const list = stations.data ?? []
  // A kiosk that was never set up follows the first station that screens incoming documents.
  const stationId = chosen ?? list.find((s) => s.intake)?.id ?? null
  const live = useQuery({
    queryKey: ['intake-live', stationId], queryFn: () => intakeStationApi.live(stationId!, 8),
    enabled: !!stationId, refetchInterval: 2000,
  })
  const choose = (id: number) => { setChosen(id); remember(id) }
  const data = live.data

  return (
    <main data-testid="intake-station-page" className="mx-auto max-w-6xl space-y-3 px-3 py-3 sm:px-4 sm:py-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-ink">Incoming documents station</h1>
        <label className="text-sm text-ink-2">Station{' '}
          <select data-testid="intake-station-select" value={stationId ?? ''} onChange={(e) => choose(Number(e.target.value))}
            className="rounded-md border border-line bg-surface px-2 py-1 text-base text-ink">
            <option value="" disabled>Choose…</option>
            {list.map((s) => <option key={s.id} value={s.id}>{s.name}{s.intake ? '' : ' (no incoming documents yet)'}</option>)}
          </select>
        </label>
      </div>
      <ErrorText error={stations.error ?? live.error} />
      {!stationId ? (
        <Card><Empty>{stations.isPending ? 'Loading…' : 'Choose the incoming documents station.'}</Empty></Card>
      ) : !data ? (
        <Card><Empty>Loading…</Empty></Card>
      ) : (
        <>
          <StatusBanner station={data.station} />
          <div className="grid items-start gap-3 sm:grid-cols-[3fr_2fr]">
            <LatestPage page={data.documents[0]} batchOpen={!!data.batch?.open} />
            <Counts live={data} />
          </div>
          <Card title={data.batch?.open ? 'Pages in this batch (newest first)' : 'Latest pages (newest first)'}>
            <PageList pages={data.documents} />
          </Card>
          <p className="text-xs text-muted">
            Passed screening only means no automatic check flagged the page. Registrar staff review every incoming document.
          </p>
          <RecentBatches stationId={stationId} />
        </>
      )}
    </main>
  )
}
