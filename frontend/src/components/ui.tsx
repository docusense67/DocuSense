import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { useId, useState } from 'react'
import type { ClientStatus, RequestStatus, Result } from '../api'
import { CLIENT_LABEL, REQUEST_LABEL } from '../lib/status'

export function Page({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <main className="mx-auto max-w-6xl px-4 py-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-ink">{title}</h1>
        {actions}
      </div>
      <div className="space-y-5">{children}</div>
    </main>
  )
}

export function Card({ title, actions, children, className = '' }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-line bg-surface p-4 ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

type Variant = 'primary' | 'secondary' | 'danger'
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent text-accent-ink hover:opacity-90',
  secondary: 'border border-line bg-surface text-ink hover:bg-surface-2',
  danger: 'bg-critical text-white hover:opacity-90',
}

export function Button({ variant = 'primary', size = 'default', busy, children, className = '', ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: 'default' | 'large'; busy?: boolean }) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={`focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 ${size === 'large' ? 'min-h-14 rounded-lg px-5 py-3 text-lg font-semibold' : 'rounded-md px-3 py-1.5 text-sm font-medium'} ${VARIANTS[variant]} ${className}`}
    >
      {busy ? 'Working…' : children}
    </button>
  )
}

const inputClass = 'mt-1 w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none'

export function Field({ label, hint, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const generatedId = useId()
  const [showPassword, setShowPassword] = useState(false)
  const isPassword = props.type === 'password'
  const inputId = props.id ?? generatedId
  return (
    <div className="block text-sm text-ink-2">
      <label htmlFor={inputId}>{label}</label>
      <div className="relative">
        <input {...props} id={inputId} type={isPassword && showPassword ? 'text' : props.type}
          className={`${inputClass}${isPassword ? ' pr-12' : ''}`} />
        {isPassword && (
          <button type="button" disabled={props.disabled} aria-controls={inputId}
            aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword}
            title={showPassword ? 'Hide password' : 'Show password'}
            onClick={() => setShowPassword((visible) => !visible)}
            className="absolute inset-y-0 right-0 mt-1 flex w-11 items-center justify-center rounded-r-md text-ink-2 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">
            <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
              <circle cx="12" cy="12" r="3" />
              {showPassword && <path d="m3 3 18 18" />}
            </svg>
          </button>
        )}
      </div>
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </div>
  )
}

export function Select({ label, children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { label: string }) {
  return (
    <label className="block text-sm text-ink-2">
      {label}
      <select {...props} className={inputClass}>{children}</select>
    </label>
  )
}

export function TextArea({ label, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string }) {
  return (
    <label className="block text-sm text-ink-2">
      {label}
      <textarea {...props} className={`${inputClass} font-mono text-xs`} />
    </label>
  )
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null
  return <p role="alert" className="text-sm text-critical">{(error as Error).message}</p>
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted">{children}</p>
}

export function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-line text-xs uppercase tracking-wide text-muted">
            {head.map((h) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">{children}</tbody>
      </table>
    </div>
  )
}

export function Td({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <td className={`px-2 py-2 align-top text-ink ${className}`}>{children}</td>
}

/* Status: always icon + label, never colour alone (dataviz status rule). */
const RESULT: Record<Result, { icon: string; label: string; cls: string }> = {
  VALID: { icon: '✓', label: 'VALID', cls: 'bg-good text-white' },
  INVALID: { icon: '✕', label: 'INVALID', cls: 'bg-critical text-white' },
  NEEDS_REVIEW: { icon: '!', label: 'NEEDS REVIEW', cls: 'bg-warning text-black' },
}

export function ResultBadge({ result, size = 'sm' }: { result: Result; size?: 'sm' | 'xl' }) {
  const r = RESULT[result]
  const sizing = size === 'xl' ? 'px-5 py-3 text-3xl gap-3' : 'px-2 py-0.5 text-xs gap-1'
  return (
    <span className={`inline-flex items-center rounded-md font-bold ${sizing} ${r.cls}`}>
      <span aria-hidden>{r.icon}</span>{r.label}
    </span>
  )
}

const badge = 'inline-block rounded-full border px-2 py-0.5 text-xs font-medium'

/** Registrar view: the workflow sub-step (S-11). */
export function RequestBadge({ status }: { status: RequestStatus }) {
  const tone = status === 'released' ? 'border-good text-good-ink'
    : status === 'rejected' || status === 'revoked' ? 'border-critical text-critical'
      : 'border-line text-ink-2'
  return <span className={`${badge} ${tone}`}>{REQUEST_LABEL[status]}</span>
}

/** Student view: Pending / Approved / Rejected / Completed (Updated Features II-B). */
export function ClientBadge({ status }: { status: ClientStatus }) {
  const tone = status === 'completed' ? 'border-good text-good-ink'
    : status === 'rejected' || status === 'revoked' ? 'border-critical text-critical'
      : 'border-line text-ink-2'
  return <span className={`${badge} ${tone}`}>{CLIENT_LABEL[status]}</span>
}

export function Stat({ label, value, tone }: { label: string; value: number | string; tone?: 'good' | 'critical' | 'warning' }) {
  const dot = tone ? { good: 'bg-good', critical: 'bg-critical', warning: 'bg-warning' }[tone] : ''
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs text-ink-2">
        {dot && <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${dot}`} />}{label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
    </div>
  )
}
