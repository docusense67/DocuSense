import { useEffect, useId, useRef } from 'react'
import { Button, ErrorText } from '../../components/ui'

export function TraySetup({ open, connected, busy, saved, error, onClose, onConnect, onMove, onStop, onSave }: {
  open: boolean; connected: boolean; busy: boolean; saved: boolean; error: unknown
  onClose: () => void; onConnect: () => void; onMove: (angle: number) => void
  onStop: () => void; onSave: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  }, [open])
  return <dialog ref={dialog} aria-labelledby={titleId} onCancel={onClose} onClose={onClose}
    className="fixed inset-0 m-auto max-h-[90vh] w-[calc(100%-2rem)] max-w-md overflow-y-auto rounded-xl border border-line bg-surface p-5 text-ink shadow-xl backdrop:bg-black/50">
    <div className="flex items-center justify-between gap-3">
      <h2 id={titleId} className="text-lg font-semibold">Tray position setup</h2>
      <button type="button" onClick={onClose} aria-label="Close tray setup" className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg text-xl text-ink-2 focus-visible:outline-2 focus-visible:outline-accent">✕</button>
    </div>
    <p className="mt-2 text-sm text-ink-2">These fixed positions are ready to use. Testing and saving setup are optional; you can feed and validate directly.</p>
    <div className="my-4 grid grid-cols-2 gap-3 text-sm">
      <div className="rounded-md border border-line p-3"><p className="font-semibold">Up · 180°</p><p className="text-ink-2">VALID tray</p></div>
      <div className="rounded-md border border-line p-3"><p className="font-semibold">Down · 90°</p><p className="text-ink-2">INVALID tray</p></div>
    </div>
    <p className="mb-3 text-xs text-muted">Keep the paper path empty while checking positions.</p>
    {!connected && <Button size="large" variant="secondary" disabled={busy} onClick={onConnect}>Connect controller</Button>}
    <div className="my-3 flex flex-wrap gap-2">
      <Button size="large" variant="secondary" disabled={!connected || busy} onClick={() => onMove(180)}>Move up · 180°</Button>
      <Button size="large" variant="secondary" disabled={!connected || busy} onClick={() => onMove(90)}>Move down · 90°</Button>
      <Button size="large" variant="danger" disabled={!connected} onClick={onStop}>Stop mechanism</Button>
    </div>
    <div className="flex flex-wrap gap-2">
      <Button size="large" disabled={!connected || busy} onClick={onSave}>Save setup</Button>
      <Button size="large" variant="secondary" onClick={onClose}>Close</Button>
    </div>
    {saved && <p className="mt-3 text-xs text-muted">A saved setup is already available for this station.</p>}
    <ErrorText error={error} />
  </dialog>
}
