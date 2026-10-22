import { useEffect, useState } from 'react'
import type { MechanismStatus } from '../../api'
import { Button } from '../../components/ui'

export function CameraPreview({ focus }: { focus?: MechanismStatus['camera_focus'] }) {
  const [enabled, setEnabled] = useState(true)
  const [frame, setFrame] = useState<{ url: string; time: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    let disposed = false
    let url: string | null = null
    let timer: ReturnType<typeof setTimeout>
    const controller = new AbortController()
    const poll = async () => {
      try {
        const response = await fetch('/api/mechanism/camera/frame', {
          credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        })
        if (!response.ok) {
          const body = await response.json()
          throw new Error(body.detail || 'Camera preview unavailable.')
        }
        const blob = await response.blob()
        if (disposed) return
        const next = URL.createObjectURL(blob)
        const previous = url
        url = next
        setFrame({ url: next, time: response.headers.get('X-Frame-Time') || '' })
        setError(null)
        if (previous) URL.revokeObjectURL(previous)
      } catch (cause) {
        if (!disposed) setError(cause instanceof Error ? cause.message : 'Camera preview unavailable.')
      } finally {
        if (!disposed) timer = setTimeout(poll, 200)
      }
    }
    void poll()
    const clock = setInterval(() => setNow(Date.now()), 1000)
    return () => {
      disposed = true
      controller.abort()
      clearTimeout(timer)
      clearInterval(clock)
      if (url) URL.revokeObjectURL(url)
    }
  }, [enabled])
  const fresh = !!frame && Number.isFinite(Date.parse(frame.time)) && now - Date.parse(frame.time) < 3000
  return <section className="mb-5 rounded-xl border border-line p-3 sm:p-5" aria-label="Live Raspberry Pi camera">
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div><h3 className="text-xl font-semibold">Live camera</h3><p role="status" className="mt-1 text-sm text-ink-2">Raspberry Pi · {enabled ? focus?.state === 'focusing' ? 'Focusing on paper…' : error || !fresh ? 'Waiting for live video' : 'LIVE' : 'Preview off'}</p></div>
      <Button size="large" variant="secondary" aria-pressed={enabled} aria-label="Camera preview"
        onClick={() => { setEnabled(!enabled); setFrame(null); setError(null) }}>{enabled ? 'Hide camera' : 'Show camera'}</Button>
    </div>
    {enabled && frame && <img src={frame.url} alt="Live view of the paper scanning area" className={`mx-auto aspect-[4/3] max-h-[65vh] w-full rounded-lg object-contain bg-black ${!fresh || error ? 'opacity-40' : ''}`} />}
    {enabled && !frame && <div className="flex min-h-64 items-center justify-center rounded-lg bg-page p-6 text-center"><p className="text-lg text-ink-2">Opening Raspberry Pi camera…</p></div>}
    {!enabled && <p className="rounded-lg bg-page p-6 text-center text-ink-2">Camera preview is hidden. Press Show camera to view the paper.</p>}
    {enabled && error && focus?.state !== 'focusing' && <p role="status" className="mt-2 text-sm text-critical">{error}</p>}
    {enabled && focus && <p role="status" className="mt-2 text-sm text-ink-2">{focus.supported === false ? 'Autofocus unavailable: ' : ''}{focus.message}</p>}
    {enabled && frame && <p className="mt-2 text-xs text-muted">Last frame: {new Date(frame.time).toLocaleTimeString()}{!fresh ? ' · Video paused / stale' : ''}</p>}
    <p className="mt-2 text-xs text-muted">Live preview refreshes up to 5 frames per second. It briefly pauses while switching to the full resolution document capture.</p>
  </section>
}
