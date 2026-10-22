import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, type Role, type User } from '../../api'
import { ReasonButton } from '../../components/forms'
import { Button, Card, ErrorText, Field, Page, Select, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'

function AccountActions({ user, isSelf }: { user: User; isSelf: boolean }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: ['users'] })
  const reset = useMutation({ mutationFn: ({ pw, reason }: { pw: string; reason: string }) => api.resetPassword(user.id, pw, reason), onSuccess: refresh })
  const deactivate = useMutation({ mutationFn: (reason: string) => api.deactivateUser(user.id, reason), onSuccess: refresh })
  if (!user.is_active) return null
  return (
    <span className="flex flex-wrap gap-2">
      <ReasonButton label={user.is_locked ? 'Unlock / reset password' : 'Reset password'} extraType="password"
        extraLabel="New temporary password (10+ characters)" hint="They must change it at next login."
        busy={reset.isPending} error={reset.error} onConfirm={(reason, pw) => reset.mutateAsync({ pw, reason })} />
      {!isSelf && <ReasonButton label="Deactivate" variant="danger" busy={deactivate.isPending} error={deactivate.error}
        onConfirm={(reason) => deactivate.mutateAsync(reason)} />}
    </span>
  )
}

function Users() {
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['users'], queryFn: api.users })
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const [form, setForm] = useState({ email: '', role: 'registrar' as Role, temporary_password: '', can_validate: false })
  const create = useMutation({
    mutationFn: () => api.createUser(form),
    onSuccess: () => { setForm({ ...form, email: '', temporary_password: '' }); qc.invalidateQueries({ queryKey: ['users'] }) },
  })
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate() }
  return (
    <Card title="Accounts">
      <form onSubmit={submit} className="mb-2 grid gap-3 sm:grid-cols-[2fr_1fr_2fr_auto_auto] sm:items-end">
        <Field label="Email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <Select label="Role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role, can_validate: false })}>
          <option value="registrar">Registrar</option><option value="admin">System Admin</option>
        </Select>
        <Field label="Temporary password" type="password" minLength={10} required value={form.temporary_password}
          onChange={(e) => setForm({ ...form, temporary_password: e.target.value })} />
        <label className="flex items-center gap-2 pb-2 text-sm text-ink-2">
          <input type="checkbox" disabled={form.role !== 'registrar'} checked={form.can_validate}
            onChange={(e) => setForm({ ...form, can_validate: e.target.checked })} /> Scan operator
        </label>
        <Button type="submit" busy={create.isPending}>Create</Button>
      </form>
      <p className="mb-3 text-xs text-muted">Student and teacher logins are created by the registrar with the student's or teacher's record, so each login is linked to it.</p>
      <ErrorText error={create.error} />
      <Table head={['Email', 'Role', 'Status', 'Last login', '']}>
        {(users.data ?? []).map((u) => (
          <tr key={u.id}>
            <Td>{u.email}</Td><Td>{u.role}{u.can_validate ? ' · operator' : ''}</Td>
            <Td>{!u.is_active ? 'Deactivated' : u.is_locked ? 'Locked' : u.must_change_password ? 'Must change password' : 'Active'}</Td>
            <Td>{formatDate(u.last_login_at, true)}</Td>
            <Td><AccountActions user={u} isSelf={u.id === me.data?.id} /></Td>
          </tr>
        ))}
      </Table>
    </Card>
  )
}

function StationRow({ s }: { s: { id: number; name: string; is_active: boolean; created_at: string } }) {
  const qc = useQueryClient()
  const off = useMutation({ mutationFn: (reason: string) => api.deactivateStation(s.id, reason), onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-stations'] }) })
  return (
    <tr>
      <Td>{s.name}</Td><Td>{s.is_active ? 'Yes' : 'No'}</Td><Td>{formatDate(s.created_at)}</Td>
      <Td>{s.is_active && <ReasonButton label="Deactivate" variant="danger" busy={off.isPending} error={off.error}
        hint="Its key stops working at once (e.g. a lost or replaced station PC)." onConfirm={(reason) => off.mutateAsync(reason)} />}</Td>
    </tr>
  )
}

function Stations() {
  const qc = useQueryClient()
  const stations = useQuery({ queryKey: ['admin-stations'], queryFn: api.adminStations })
  const [name, setName] = useState('')
  const register = useMutation({
    mutationFn: () => api.registerStation(name),
    onSuccess: () => { setName(''); qc.invalidateQueries({ queryKey: ['admin-stations'] }) },
  })
  return (
    <Card title="Scan stations">
      <form onSubmit={(e) => { e.preventDefault(); register.mutate() }} className="mb-3 flex items-end gap-2">
        <Field label="Station name" required value={name} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" busy={register.isPending}>Register</Button>
      </form>
      {register.data && (
        <div className="mb-3 rounded-md border border-warning p-3 text-sm">
          <p className="font-semibold text-ink">! API key for {register.data.name} (shown once; copy it to the station now)</p>
          <code className="mt-1 block break-all text-xs text-ink">{register.data.api_key}</code>
        </div>
      )}
      <ErrorText error={register.error} />
      <Table head={['Name', 'Active', 'Registered', '']}>
        {(stations.data ?? []).map((s) => <StationRow key={s.id} s={s} />)}
      </Table>
    </Card>
  )
}

export function AdminHome() {
  return (
    <Page title="Accounts & stations">
      <Users />
      <Stations />
    </Page>
  )
}
