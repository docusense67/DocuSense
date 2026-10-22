import { type ReactNode, useId, useState } from 'react'
import { ApiError, type ImportErrors } from '../api'
import { Button, ErrorText } from './ui'

/**
 * A button that asks for a reason (and optionally one more value) before it acts.
 * Registrar/admin changes to official records always carry a reason (Section 4).
 */
export function ReasonButton({ label, confirmLabel, variant = 'secondary', onConfirm, busy, error, extraLabel, extraType = 'text', hint }: {
  label: string
  confirmLabel?: string
  variant?: 'primary' | 'secondary' | 'danger'
  onConfirm: (reason: string, extra: string) => Promise<unknown>
  busy?: boolean
  error?: unknown
  extraLabel?: string
  extraType?: 'text' | 'password' | 'email'
  hint?: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [extra, setExtra] = useState('')
  const id = useId()
  if (!open) return <Button variant={variant} onClick={() => setOpen(true)}>{label}</Button>
  const ready = reason.trim().length >= 3 && (!extraLabel || extra.trim().length > 0)
  const submit = () => onConfirm(reason.trim(), extra.trim()).then(() => { setOpen(false); setReason(''); setExtra('') }, () => {})
  return (
    <div className="w-full max-w-sm space-y-2 rounded-md border border-line p-2">
      {extraLabel && (
        <label className="block text-xs text-ink-2" htmlFor={`${id}-x`}>{extraLabel}
          <input id={`${id}-x`} type={extraType} value={extra} onChange={(e) => setExtra(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink" />
        </label>
      )}
      <label className="block text-xs text-ink-2" htmlFor={`${id}-r`}>Reason (kept in the audit trail)
        <input id={`${id}-r`} autoFocus={!extraLabel} value={reason} onChange={(e) => setReason(e.target.value)}
          className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink" />
      </label>
      {hint && <p className="text-xs text-muted">{hint}</p>}
      <div className="flex gap-2">
        <Button variant={variant} disabled={!ready} busy={busy} onClick={submit}>{confirmLabel ?? label}</Button>
        <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
      <ErrorText error={error} />
    </div>
  )
}

/** Upload a CSV/Excel file; shows every rejected row (imports are all-or-nothing). */
export function ImportForm({ label, onUpload, busy, error, done }: {
  label: string
  onUpload: (file: File) => void
  busy?: boolean
  error?: unknown
  done?: ReactNode
}) {
  const [file, setFile] = useState<File | null>(null)
  const report = error instanceof ApiError ? (error.detail as Partial<ImportErrors>) : null
  const rows = report?.errors ?? []
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-sm text-ink-2">{label}
          <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block text-sm text-ink file:mr-2 file:rounded-md file:border file:border-line file:bg-surface file:px-2 file:py-1 file:text-ink" />
        </label>
        <Button disabled={!file} busy={busy} onClick={() => file && onUpload(file)}>Upload</Button>
      </div>
      {rows.length > 0 ? (
        <div role="alert" className="rounded-md border border-critical p-2 text-sm">
          <p className="font-semibold text-critical">✕ {report?.detail}</p>
          <ul className="mt-1 max-h-60 list-inside list-disc overflow-y-auto text-ink">
            {rows.map((r) => <li key={r.row}>Row {r.row}: {r.errors.join('; ')}</li>)}
          </ul>
        </div>
      ) : <ErrorText error={error} />}
      {done}
    </div>
  )
}
