import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type User } from '../api'
import { formatDate } from '../lib/format'

export function Notifications({ user }: { user: User }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const bell = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const qc = useQueryClient()
  const mutations = useIsMutating({ predicate: (mutation) => mutation.options.mutationKey?.[0] !== 'read-notifications' })
  const enabled = user.role !== 'teacher'
  const query = useQuery({
    queryKey: ['notifications', user.id], queryFn: api.notifications,
    enabled, refetchInterval: 15000,
  })
  useEffect(() => {
    if (enabled && mutations === 0) qc.invalidateQueries({ queryKey: ['notifications', user.id] })
  }, [enabled, mutations, qc, user.id])
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setOpen(false); bell.current?.focus() }
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])
  const items = query.data ?? []
  const unread = items.filter((item) => !item.read).length
  const readMutation = useMutation({
    mutationKey: ['read-notifications'], mutationFn: api.readNotifications,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications', user.id] }),
  })
  const markRead = (ids: number[]) => readMutation.mutate(ids)
  if (!enabled) return null
  return (
    <div ref={root} className="relative">
      <button ref={bell} type="button" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}
        aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}
        className="relative flex h-11 w-11 items-center justify-center rounded-md hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent">
        <svg aria-hidden="true" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
        </svg>
        {unread > 0 && <span aria-hidden="true" className="absolute right-0 top-0 rounded-full bg-critical px-1.5 text-xs font-semibold text-white">{unread}</span>}
      </button>
      {open && (
        <section id={panelId} aria-label="Notifications" className="absolute right-0 z-50 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-xl border border-line bg-surface shadow-lg">
          <div className="flex items-center justify-between border-b border-line p-3">
            <h2 className="font-semibold text-ink">Notifications</h2>
            <button type="button" disabled={!unread} onClick={() => markRead(items.map((item) => item.id))}
              className="text-xs text-accent hover:underline disabled:opacity-50">Mark all as read</button>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {readMutation.isError && <p role="alert" className="p-3 text-xs text-critical">Could not save read status. Try again.</p>}
            {query.isPending && <p className="p-4 text-sm text-muted">Loading notifications…</p>}
            {query.isError && <div role="alert" className="p-4 text-sm text-critical">Could not load notifications. <button type="button" onClick={() => query.refetch()} className="underline">Retry</button></div>}
            {query.isSuccess && !items.length && <p className="p-4 text-sm text-muted">No notifications yet.</p>}
            {items.map((item) => (
              <Link key={item.id} to={item.href} onClick={() => { markRead([item.id]); setOpen(false) }}
                className={`block border-b border-line p-3 last:border-0 hover:bg-surface-2 ${!item.read ? 'bg-accent/5' : ''}`}>
                <p className="text-sm font-medium text-ink">{!item.read && <span aria-label="Unread" className="mr-2 inline-block h-2 w-2 rounded-full bg-accent" />}{item.title}</p>
                <p className="mt-1 text-xs text-ink-2">{item.message}</p>
                <time dateTime={item.timestamp} className="mt-1 block text-xs text-muted">{formatDate(item.timestamp)}</time>
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
