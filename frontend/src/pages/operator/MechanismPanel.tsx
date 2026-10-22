import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type MechanismCommand, type MechanismStatus } from '../../api'
import { Button, Card, ErrorText, ResultBadge } from '../../components/ui'
import { ScanProgress } from './ScanProgress'
import { CameraPreview } from './CameraPreview'
import { CurrentScanText } from './ScannedPaperText'
import { TraySetup } from './TraySetup'
import { hasTraySetup, saveTraySetup } from '../../lib/traySetup'

export function MechanismPanel({ status, error, sessionId, stationId, ensureSession, offline }: {
  status?: MechanismStatus; error: unknown; sessionId: number | null; stationId: number | null
  ensureSession: () => Promise<number>; offline: boolean
}) {
  const qc = useQueryClient()
  const generation = useRef(0)
  const movingRef = useRef(false)
  useEffect(() => () => { generation.current += 1 }, [])
  const [removed, setRemoved] = useState(false)
  const [trayOpen, setTrayOpen] = useState(false)
  const [traySaved, setTraySaved] = useState(() => hasTraySetup(stationId))
  const [trayStorageBlocked, setTrayStorageBlocked] = useState(false)
  const workflow = status?.workflow
  const active = !!workflow?.active
  const held = !!workflow?.held
  const command = useMutation({
    mutationFn: async (body: MechanismCommand) => {
      const requestGeneration = generation.current
      if (body.action === 'scan') {
        const id = await ensureSession()
        if (generation.current !== requestGeneration) throw new Error('Feed cancelled.')
        const result = await api.mechanismCommand({ ...body, session_id: id })
        if (generation.current !== requestGeneration) {
          await api.mechanismCommand({ action: 'stop' })
          throw new Error('Feed cancelled.')
        }
        return result
      }
      const result = await api.mechanismCommand(body)
      if (generation.current !== requestGeneration) {
        await api.mechanismCommand({ action: 'stop' })
        throw new Error('Movement cancelled.')
      }
      return result
    },
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['mechanism'] }),
        qc.invalidateQueries({ queryKey: ['session', sessionId] }),
      ])
      setRemoved(false)
    },
  })
  const stop = useMutation({
    mutationFn: () => api.mechanismCommand({ action: 'stop' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mechanism'] }),
  })
  const moving = active || !!status?.busy || (command.isPending &&
    ['scan', 'tray', 'test-motor', 'test-servo1', 'test-servo2', 'test-servo3'].includes(command.variables?.action ?? ''))
  useEffect(() => { movingRef.current = moving }, [moving])
  useEffect(() => {
    if (!moving) return
    const heartbeat = () => { void api.mechanismCommand({ action: 'heartbeat' }).catch(() => {}) }
    heartbeat()
    const interval = window.setInterval(heartbeat, 1000)
    return () => { window.clearInterval(interval) }
  }, [moving])
  useEffect(() => {
    const stopOnExit = () => {
      if (!movingRef.current) return
      void fetch('/api/mechanism/command', { method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'stop' }), keepalive: true }).catch(() => {})
    }
    window.addEventListener('pagehide', stopOnExit)
    return () => { window.removeEventListener('pagehide', stopOnExit); stopOnExit() }
  }, [])

  if (!status?.enabled && !error) return null
  const ready = status?.enabled && stationId === status.station_id && !offline
  const disabled = active || held || command.isPending || stop.isPending || !!status?.busy
  const manualDisabled = disabled || !ready || !!error || !status?.connected
  const startingScan = command.isPending && command.variables?.action === 'scan'
  return (
    <Card>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-2xl font-bold">Paper scanner</h2><p className="mt-1 text-sm text-ink-2">Place one sheet in the feeder, then press Start.</p></div>
        <span role="status" className="rounded-full border border-line bg-page px-4 py-2 text-sm font-semibold">{!ready || error ? 'Station unavailable' : held ? 'Paper needs attention' : active || startingScan ? 'Scan in progress' : moving ? 'Mechanism running' : 'Ready to start'}</span>
      </div>
      <div className="sticky top-2 z-20 mb-5 grid grid-cols-1 gap-3 rounded-xl border border-line bg-surface p-3 shadow-md sm:grid-cols-2">
        <Button size="large" className="flex min-h-24 items-center justify-center gap-3" disabled={!ready || disabled || !!error}
          aria-label="Start: feed one sheet and validate" aria-busy={startingScan}
          onClick={() => command.mutate({ action: 'scan' })}>
          <span aria-hidden="true" className="text-3xl">▶</span><span className="text-left"><span className="block text-2xl">{startingScan ? 'Starting…' : active ? 'Scanning…' : 'Start / Feed paper'}</span><span className="block text-sm font-normal">Feed one sheet &amp; validate</span></span>
        </Button>
        <Button size="large" variant="danger" className="flex min-h-24 items-center justify-center gap-3" aria-busy={stop.isPending}
          disabled={stop.isPending} onClick={() => { generation.current += 1; stop.mutate() }}>
          <span aria-hidden="true" className="text-3xl">■</span><span className="text-left"><span className="block text-2xl">{stop.isPending ? 'Stopping…' : 'Stop mechanism'}</span><span className="block text-sm font-normal">Stop motor and servos</span></span>
        </Button>
      </div>
      {!ready && <p className="mb-4 text-sm text-critical">Choose an online camera station to start.</p>}
      <div className="mb-4"><ErrorText error={error ?? command.error ?? stop.error ?? (status?.error ? new Error(status.error) : null)} /></div>
      <ScanProgress workflow={workflow} pending={startingScan} unavailable={!!error || offline} />
      {held && (!active || workflow?.stage === 'stopping') && <section aria-label="Resume after stopped scan" className="mb-4 rounded-md border border-warning p-3 text-sm">
        <p className="font-semibold">Clear the paper path to start another scan</p>
        <p className="mt-1">The previous scan stopped or needs review. Remove the sheet before resetting; the station will not feed again automatically.</p>
        {active ? <p role="status" className="mt-2 text-ink-2">Waiting for the camera/OCR operation to finish stopping…</p>
          : <>
            <label className="my-3 flex min-h-12 items-center gap-3"><input type="checkbox" className="h-6 w-6" checked={removed}
              onChange={(event) => setRemoved(event.target.checked)} />I removed the paper and cleared the path.</label>
            <Button size="large" variant="secondary" disabled={!removed || stop.isPending || !!error} busy={command.isPending}
              onClick={() => command.mutate({ action: 'acknowledge' })}>Clear stop · Ready for next sheet</Button>
          </>}
      </section>}

      {status?.enabled && stationId === status.station_id && <>
        <CameraPreview focus={status.camera_focus} />
        <CurrentScanText scanId={startingScan ? null : workflow?.result?.scanned_document_id ?? null}
          waiting={startingScan || (active && !workflow?.result)}
          interrupted={!active && (held || workflow?.stage === 'error')} />
      </>}
      <details className="mb-4 rounded-xl border border-line bg-page p-4">
      <summary className="min-h-12 cursor-pointer text-lg font-semibold">Motor &amp; servo controls <span className="text-sm font-normal text-ink-2">· Optional tests</span></summary>
      <section aria-label="Motor and servo controls" className="mt-3">
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm font-medium">Motor & servo controls</p>
          <span className="text-xs text-muted">{status?.connected ? 'Controller connected' : 'Controller disconnected'}</span>
          <Button size="large" variant="secondary" disabled={disabled || !ready || !!error || !!status?.connected}
            onClick={() => command.mutate({ action: 'connect' })}>Connect controller</Button>
          <Button size="large" variant="secondary" disabled={active || command.isPending || stop.isPending || !status?.connected}
            onClick={() => { generation.current += 1; command.mutate({ action: 'disconnect' }) }}>Disconnect</Button>
        </div>
        <p className="my-3 text-xs text-muted">Clear the paper path before testing. Motor: GPIO 17 / 27 / 18. Feeder: GPIO 12. Eject: GPIO 13. Sorting: GPIO 19. Tests stop automatically.</p>
        <div className="flex flex-wrap gap-2">
          <Button size="large" variant="secondary" disabled={manualDisabled} onClick={() => command.mutate({ action: 'test-motor' })}>Test motor · 5 sec</Button>
          <Button size="large" variant="secondary" disabled={manualDisabled} onClick={() => command.mutate({ action: 'test-servo1' })}>Test feeder · 90° / 180° / 90°</Button>
          <Button size="large" variant="secondary" disabled={manualDisabled} onClick={() => command.mutate({ action: 'test-servo2' })}>Test eject servo · 8 sec</Button>
        </div>
        {status?.connected && <p role="status" className="mt-3 text-sm text-ink-2">{status.label ?? 'Controller ready'}{status.state === 'cycle' ? ` · ${Math.ceil(status.remaining ?? 0)} sec in this step` : ''}</p>}
      </section>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Button size="large" variant="secondary" disabled={disabled || !ready || !!error}
          onClick={() => { command.reset(); setTrayOpen(true) }}>Tray position setup</Button>
        <span className="text-xs text-muted">{traySaved ? trayStorageBlocked ? 'Setup ready for this page · browser storage unavailable' : 'Setup saved · ready for repeated scans' : 'Fixed positions ready · Up 180° / Down 90°. Setup is optional.'}</span>
      </div>
      </details>
      <TraySetup open={trayOpen} connected={!!status?.connected}
        busy={disabled || !!error || !ready} saved={traySaved} error={command.error ?? stop.error}
        onClose={() => setTrayOpen(false)} onConnect={() => command.mutate({ action: 'connect' })}
        onMove={(angle) => command.mutate({ action: 'test-servo3', angle })}
        onStop={() => { generation.current += 1; stop.mutate() }}
        onSave={() => {
          if (!stationId) return
          setTrayStorageBlocked(!saveTraySetup(stationId))
          setTraySaved(true)
          setTrayOpen(false)
        }} />
      <div className="flex flex-wrap items-center gap-3">
        {workflow?.result && !startingScan && <><ResultBadge result={workflow.result.result} size="xl" /><Link className="flex min-h-14 items-center rounded-lg border border-line px-4 text-base font-semibold text-accent underline" to={`/scan/${workflow.result.scanned_document_id}`}>View scan result</Link></>}
      </div>
    </Card>
  )
}
