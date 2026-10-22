import { useMutation, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, type ReactNode, useState } from 'react'
import { api } from '../api'
import { Button, ErrorText, Field } from '../components/ui'

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-page px-4">
      <div className="w-full max-w-sm rounded-xl border border-line bg-surface p-6">
        <p className="mb-1 text-xs text-muted">Academic Document System</p>
        <h1 className="mb-4 text-lg font-semibold text-ink">{title}</h1>
        {children}
      </div>
    </main>
  )
}

export function Login() {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({
    mutationFn: () => api.login(email, password),
    onSuccess: (user) => qc.setQueryData(['me'], user),
  })
  const submit = (e: FormEvent) => { e.preventDefault(); login.mutate() }
  return (
    <Shell title="Sign in">
      <form onSubmit={submit} className="space-y-3">
        <Field label="Email" type="email" autoComplete="username" required value={email}
          onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" type="password" autoComplete="current-password" required value={password}
          onChange={(e) => setPassword(e.target.value)} />
        <ErrorText error={login.error} />
        <Button type="submit" busy={login.isPending} className="w-full">Sign in</Button>
      </form>
    </Shell>
  )
}

export function ChangePassword() {
  const qc = useQueryClient()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const change = useMutation({
    mutationFn: () => api.changePassword(current, next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['me'] }),
  })
  const submit = (e: FormEvent) => { e.preventDefault(); change.mutate() }
  return (
    <Shell title="Set a new password">
      <p className="mb-4 text-sm text-ink-2">Change your temporary password before continuing. Use at least 10 characters.</p>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Current password" type="password" autoComplete="current-password" required value={current}
          onChange={(e) => setCurrent(e.target.value)} />
        <Field label="New password" type="password" autoComplete="new-password" required minLength={10} value={next}
          onChange={(e) => setNext(e.target.value)} />
        <ErrorText error={change.error} />
        <Button type="submit" busy={change.isPending} className="w-full">Save password</Button>
      </form>
    </Shell>
  )
}
