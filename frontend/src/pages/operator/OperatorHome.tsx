import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type ScanFilters } from '../../api'
import { Button, Card, Empty, ErrorText, Page, ResultBadge, Stat, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { ScanResultView } from './ScanResultView'
import { MechanismPanel } from './MechanismPanel'

// Per-viewer convenience only (which station this browser sits at); safe to lose.
function remember(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value) } catch { /* storage blocked */ }
}
function recall(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}

const STATION_STATE: Record<string, string> = {
  ready: '● Ready', scanning: '● Scanning', jam: '! Paper jam: clear and re-feed',
  double_feed: '! Double feed: re-feed the sheet', offline: '✕ Offline',
}

function StationBar({ stationId, setStationId }: { stationId: number | null; setStationId: (id: number) => void }) {
  const stations = useQuery({ queryKey: ['stations'], queryFn: api.stations, refetchInterval: 5000 })
  const station = stations.data?.find((s) => s.id === stationId)
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <label className="text-ink-2">Station{' '}
        <select value={stationId ?? ''} onChange={(e) => setStationId(Number(e.target.value))}
          className="min-h-12 max-w-full rounded-lg border border-line bg-surface px-4 py-2 text-base text-ink">
          <option value="" disabled>Choose…</option>
          {(stations.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      {station && (station.silent && station.status !== 'offline' ? (
        // The station service reports every 15 s; silence means it is not running.
        <span className="text-ink-2">
          ○ {station.last_seen_at ? `No signal from the station since ${formatDate(station.last_seen_at, true)}` : 'No status reported yet'}
        </span>
      ) : (
        <span className={station.status === 'jam' || station.status === 'double_feed' || station.status === 'offline' ? 'font-semibold text-critical' : 'text-ink-2'}>
          {STATION_STATE[station.status ?? ''] ?? '○ No status reported yet'}
          {station.status_detail && <span className="text-muted"> · {station.status_detail}</span>}
        </span>
      ))}
    </div>
  )
}

const filterClass = 'rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink'

function Filters({ value, onChange }: { value: ScanFilters; onChange: (f: ScanFilters) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-ink-2">
      <select aria-label="Filter by result" value={value.result ?? ''} className={filterClass}
        onChange={(e) => onChange({ ...value, result: e.target.value as ScanFilters['result'] })}>
        <option value="">All results</option><option value="VALID">Valid</option>
        <option value="INVALID">Invalid</option><option value="NEEDS_REVIEW">Needs review</option>
      </select>
      <select aria-label="Filter by document type" value={value.document_type ?? ''} className={filterClass}
        onChange={(e) => onChange({ ...value, document_type: e.target.value as ScanFilters['document_type'] })}>
        <option value="">All types</option><option value="TOR">TOR</option><option value="COE">COE</option>
      </select>
      <label>From <input type="date" value={value.date_from ?? ''} className={filterClass}
        onChange={(e) => onChange({ ...value, date_from: e.target.value })} /></label>
      <label>To <input type="date" value={value.date_to ?? ''} className={filterClass}
        onChange={(e) => onChange({ ...value, date_to: e.target.value })} /></label>
    </div>
  )
}

function Insights() {
  const [filters, setFilters] = useState<ScanFilters>({})
  const recent = useQuery({ queryKey: ['recent-scans', filters], queryFn: () => api.recentScans(filters), refetchInterval: 10_000 })
  const stats = useQuery({ queryKey: ['scan-stats', filters], queryFn: () => api.scanStats(filters), refetchInterval: 10_000 })
  return (
    <div className="grid items-start gap-5 lg:grid-cols-[2fr_1fr]">
      <Card title="Recent scans" actions={
        <span className="flex gap-2 text-xs">
          Export:
          <a href={api.exportUrl('csv', undefined, filters)} className="text-accent hover:underline">CSV</a>
          <a href={api.exportUrl('pdf', undefined, filters)} className="text-accent hover:underline">PDF</a>
        </span>
      }>
        <div className="mb-3"><Filters value={filters} onChange={setFilters} /></div>
        {recent.isPending ? <Empty>Loading…</Empty> : (recent.data ?? []).length === 0 ? <Empty>No scans match.</Empty> : (
          <Table head={['Document', 'Result', 'Reason', 'When']}>
            {recent.data!.map((r) => (
              <tr key={r.scanned_document_id}>
                <Td className="whitespace-nowrap"><Link to={`/scan/${r.scanned_document_id}`} className="num text-accent hover:underline">{r.document_number ?? '?'}</Link></Td>
                <Td>{r.result && <ResultBadge result={r.result} />}{r.final_result && <span className="block text-xs text-muted">reviewed: {r.final_result}</span>}</Td>
                <Td className="text-ink-2">{r.top_reason}</Td><Td>{formatDate(r.scanned_at, true)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Card title="Fields that fail most">
        <p className="mb-2 text-xs text-muted">Many low-confidence reads of one field usually means a scanning or zone problem, not tampering.</p>
        {(stats.data?.failed_fields ?? []).length === 0 ? <Empty>No failed fields.</Empty> : (
          <Table head={['Field', 'Failures']}>
            {stats.data!.failed_fields.map((f) => (
              <tr key={f.field}><Td>{f.field.replaceAll('_', ' ')}</Td><Td className="num">{f.count}</Td></tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  )
}

export function OperatorHome() {
  const qc = useQueryClient()
  const mechanism = useQuery({ queryKey: ['mechanism'], queryFn: api.mechanismStatus, refetchInterval: 1000, refetchIntervalInBackground: true, retry: false })
  const [stationId, setStationIdState] = useState<number | null>(() => Number(recall('adgvs.station')) || null)
  const [sessionId, setSessionIdState] = useState<number | null>(() => Number(recall('adgvs.session')) || null)
  const [pinned, setPinned] = useState<number | null>(null)
  const feedSession = useRef<{ id: number; stationId: number } | null>(null)

  const setStationId = (id: number) => { setStationIdState(id); remember('adgvs.station', String(id)) }
  const setSessionId = (id: number | null) => { setSessionIdState(id); remember('adgvs.session', id === null ? null : String(id)) }

  const session = useQuery({
    queryKey: ['session', sessionId], queryFn: () => api.session(sessionId!),
    enabled: !!sessionId, refetchInterval: (q) => (q.state.data?.ended_at ? false : 2000),
  })
  const docs = session.data?.documents ?? []
  const latest = docs.length ? docs[docs.length - 1].scanned_document_id : null
  const shown = pinned ?? latest
  const detail = useQuery({ queryKey: ['scan', shown], queryFn: () => api.scanDetail(shown!), enabled: !!shown })

  const stations = useQuery({ queryKey: ['stations'], queryFn: api.stations, refetchInterval: 5000 })
  const offline = stations.data?.find((s) => s.id === stationId)?.status === 'offline'
  const start = useMutation({
    mutationFn: () => api.startSession('single', stationId!),
    onSuccess: (s) => {
      feedSession.current = { id: s.session_id, stationId: stationId! }
      setSessionId(s.session_id); setPinned(null)
    },
  })
  const end = useMutation({
    mutationFn: () => api.endSession(sessionId!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['session', sessionId] }),
  })
  const open = session.data && !session.data.ended_at
  const cameraStation = mechanism.data?.enabled && stationId === mechanism.data.station_id
  const ensureFeedSession = async () => {
    if (!stationId) throw new Error('Choose the camera station first.')
    const current = feedSession.current
    if (current && current.stationId === stationId) {
      const latest = await api.session(current.id)
      if (!latest.ended_at) return current.id
    }
    const created = await start.mutateAsync()
    return created.session_id
  }

  return (
    <Page title="Scan station" actions={<StationBar stationId={stationId} setStationId={setStationId} />}>
      {!cameraStation && <Card title="Paper feed">
        <div className="flex flex-wrap items-center gap-2">
          <Button size="large" onClick={() => start.mutate()} disabled={!stationId || offline || !!mechanism.data?.busy} busy={start.isPending}>Start scanning</Button>
          {!stationId && <span className="text-sm text-muted">Choose a station first.</span>}
          {offline && <span className="text-sm font-semibold text-critical">Station offline: reconnect it before scanning.</span>}
        </div>
        <p className="mt-3 text-sm text-ink-2">Choose a station and start scanning. Results appear below.</p>
        <ErrorText error={start.error ?? end.error} />
      </Card>}

      <MechanismPanel key={stationId ?? 'no-station'} status={mechanism.data} error={mechanism.error} sessionId={sessionId} stationId={stationId}
        ensureSession={ensureFeedSession} offline={!!offline} />

      {session.data && (
        <div className="grid items-start gap-5 lg:grid-cols-[1fr_2fr]">
          <Card title={`Session ${session.data.session_id}${session.data.ended_at ? ' (ended)' : ''}`} actions={
            <span className="flex gap-2 text-xs">
              <a href={api.exportUrl('csv', session.data.session_id)} className="text-accent hover:underline">CSV</a>
              <a href={api.exportUrl('pdf', session.data.session_id)} className="text-accent hover:underline">PDF</a>
            </span>
          }>
            {open && <div className="mb-4"><Button size="large" variant="secondary" onClick={() => end.mutate()} disabled={!!mechanism.data?.busy || !!mechanism.data?.workflow?.active || start.isPending} busy={end.isPending}>End session</Button><ErrorText error={end.error} /></div>}
            <div className="mb-3 grid grid-cols-2 gap-2">
              <Stat label="Total" value={session.data.total} />
              <Stat label="✓ Valid" value={session.data.totals.VALID} tone="good" />
              <Stat label="✕ Invalid" value={session.data.totals.INVALID} tone="critical" />
              <Stat label="! Review" value={session.data.totals.NEEDS_REVIEW} tone="warning" />
            </div>
            {docs.length === 0 ? <Empty>Waiting for the station to send results…</Empty> : (
              <Table head={['#', 'Document', 'Result']}>
                {docs.map((d) => (
                  <tr key={d.scanned_document_id} className={d.scanned_document_id === shown ? 'bg-surface-2' : ''}>
                    <Td className="num">{d.position ?? '—'}</Td>
                    <Td><button onClick={() => setPinned(d.scanned_document_id)} className="num text-left text-accent hover:underline"
                      aria-current={d.scanned_document_id === shown}>{d.document_number ?? '?'}</button>
                      <span className="block text-xs text-muted">{d.top_reason}</span></Td>
                    <Td>{d.result && <ResultBadge result={d.result} />}</Td>
                  </tr>
                ))}
              </Table>
            )}
            {pinned && <button onClick={() => setPinned(null)} className="mt-2 text-xs text-accent hover:underline">Follow latest result</button>}
          </Card>
          <div>{detail.data ? <ScanResultView detail={detail.data} /> : <Card><Empty>The latest result will appear here.</Empty></Card>}</div>
        </div>
      )}

      <details className="rounded-xl border border-line bg-surface p-4">
        <summary className="min-h-12 cursor-pointer text-lg font-semibold">Scan history &amp; reports</summary>
        <div className="mt-4"><Insights /></div>
      </details>
    </Page>
  )
}
