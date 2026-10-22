import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type StudentRow } from '../../api'
import { ImportForm, ReasonButton } from '../../components/forms'
import { Button, Card, Empty, ErrorText, Field, Page, Select, Table, Td } from '../../components/ui'

function NewStudent() {
  const qc = useQueryClient()
  const programs = useQuery({ queryKey: ['programs'], queryFn: api.programs })
  const empty = { student_number: '', first_name: '', middle_name: '', last_name: '', program_id: '', year_level: '1', email: '', temporary_password: '' }
  const [form, setForm] = useState(empty)
  const set = (k: keyof typeof empty) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  const create = useMutation({
    mutationFn: () => api.createStudent({
      student_number: form.student_number, first_name: form.first_name, last_name: form.last_name,
      middle_name: form.middle_name || null, program_id: form.program_id ? Number(form.program_id) : null,
      year_level: Number(form.year_level),
      ...(form.email ? { email: form.email, temporary_password: form.temporary_password } : {}),
    }),
    onSuccess: () => { setForm(empty); qc.invalidateQueries({ queryKey: ['students'] }) },
  })
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate() }
  return (
    <Card title="Add student">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-4">
        <Field label="Student number" required value={form.student_number} onChange={set('student_number')} />
        <Field label="First name" required value={form.first_name} onChange={set('first_name')} />
        <Field label="Middle name" value={form.middle_name} onChange={set('middle_name')} />
        <Field label="Last name" required value={form.last_name} onChange={set('last_name')} />
        <Select label="Program" value={form.program_id} onChange={set('program_id')}>
          <option value="">—</option>
          {(programs.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
        </Select>
        <Field label="Year level" type="number" min={1} max={6} value={form.year_level} onChange={set('year_level')} />
        <Field label="Login email (optional)" type="email" value={form.email} onChange={set('email')} />
        <Field label="Temporary password" type="password" minLength={10} required={!!form.email}
          value={form.temporary_password} onChange={set('temporary_password')} hint="Must change on first login" />
        <div className="sm:col-span-4"><Button type="submit" busy={create.isPending}>Add student</Button></div>
      </form>
      <ErrorText error={create.error} />
      {create.isSuccess && <p className="mt-2 text-sm text-good-ink">Student added.</p>}
    </Card>
  )
}

function ImportStudents() {
  const qc = useQueryClient()
  const run = useMutation({ mutationFn: api.importStudents, onSuccess: () => qc.invalidateQueries({ queryKey: ['students'] }) })
  return (
    <Card title="Import students (CSV or Excel)">
      <p className="mb-2 text-xs text-muted">Columns and an example file are in the import templates guide. If any row has a problem, nothing is imported and every problem is listed.</p>
      <ImportForm label="Student list" onUpload={(f) => run.mutate(f)} busy={run.isPending} error={run.error}
        done={run.data && <p className="text-sm text-good-ink">✓ {run.data.imported} student(s) imported. Create their logins from the list below.</p>} />
    </Card>
  )
}

function LoginForm({ onCreate, busy, error }: { onCreate: (email: string, pw: string) => Promise<unknown>; busy: boolean; error: unknown }) {
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [pw, setPw] = useState('')
  if (!open) return <Button variant="secondary" onClick={() => setOpen(true)}>Create login</Button>
  return (
    <form onSubmit={(e) => { e.preventDefault(); onCreate(email, pw).then(() => setOpen(false), () => {}) }}
      className="w-full max-w-sm space-y-2 rounded-md border border-line p-2">
      <Field label="Login email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Temporary password" type="password" required minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} hint="Must change on first login" />
      <div className="flex gap-2"><Button type="submit" busy={busy}>Create login</Button>
        <Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button></div>
      <ErrorText error={error} />
    </form>
  )
}

function StudentActions({ s }: { s: StudentRow }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: ['students'] })
  const login = useMutation({ mutationFn: ({ email, pw }: { email: string; pw: string }) => api.createStudentLogin(s.id, email, pw), onSuccess: refresh })
  const reset = useMutation({ mutationFn: ({ pw, reason }: { pw: string; reason: string }) => api.resetPassword(s.user_id!, pw, reason) })
  const deactivate = useMutation({ mutationFn: (reason: string) => api.deactivateUser(s.user_id!, reason), onSuccess: refresh })
  const status = useMutation({
    mutationFn: (reason: string) => api.updateStudent(s.id, { status: s.status === 'active' ? 'inactive' : 'active', reason }),
    onSuccess: refresh,
  })
  return (
    <div className="flex flex-wrap gap-2">
      {!s.user_id ? (
        <LoginForm onCreate={(email, pw) => login.mutateAsync({ email, pw })} busy={login.isPending} error={login.error} />
      ) : s.login_active === false ? (
        <span className="self-center text-xs text-muted">Login deactivated</span>
      ) : (
        <>
          <ReasonButton label="Reset password" extraLabel="New temporary password (10+ characters)" extraType="password"
            hint="Also unlocks the account. The student must change it at next login." busy={reset.isPending} error={reset.error}
            onConfirm={(reason, pw) => reset.mutateAsync({ pw, reason })} />
          <ReasonButton label="Deactivate login" variant="danger" busy={deactivate.isPending} error={deactivate.error}
            onConfirm={(reason) => deactivate.mutateAsync(reason)} />
        </>
      )}
      <ReasonButton label={s.status === 'active' ? 'Mark inactive' : 'Mark active'} busy={status.isPending} error={status.error}
        onConfirm={(reason) => status.mutateAsync(reason)} />
      {reset.isSuccess && <span className="text-xs text-good-ink">✓ Password reset.</span>}
      {deactivate.isSuccess && <span className="text-xs text-good-ink">✓ Login deactivated.</span>}
    </div>
  )
}

function Students() {
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const students = useQuery({ queryKey: ['students', q], queryFn: () => api.students(q) })
  return (
    <Card title="Students" actions={
      <input aria-label="Search students" placeholder="Search name or number" value={q} onChange={(e) => setQ(e.target.value)}
        className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink" />
    }>
      {students.isPending ? <Empty>Loading…</Empty> : (students.data ?? []).length === 0 ? <Empty>No students found.</Empty> : (
        <Table head={['Number', 'Name', 'Year', 'Status', 'Login', '']}>
          {students.data!.map((s) => (
            <tr key={s.id}>
              <Td className="num">{s.student_number}</Td>
              <Td>{s.last_name}, {s.first_name} {s.middle_name ?? ''}
                {selected === s.id && <div className="mt-2"><StudentActions s={s} /></div>}</Td>
              <Td>{s.year_level ?? '—'}</Td><Td>{s.status}</Td><Td>{!s.user_id ? 'No' : s.login_active === false ? 'Deactivated' : 'Yes'}</Td>
              <Td><span className="flex gap-3">
                <Link to={`/records/students/${s.id}`} className="text-xs text-accent hover:underline" data-testid="student-record-link">Record</Link>
                <button onClick={() => setSelected(selected === s.id ? null : s.id)} className="text-xs text-accent hover:underline"
                  aria-expanded={selected === s.id}>{selected === s.id ? 'Close' : 'Manage'}</button>
              </span></Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}

/** Section 5C: grade edits after a term is locked need registrar approval and a reason. */
function GradeChanges() {
  const qc = useQueryClient()
  const pending = useQuery({ queryKey: ['grade-changes'], queryFn: () => api.changeRequests('pending') })
  const refresh = () => qc.invalidateQueries({ queryKey: ['grade-changes'] })
  const approve = useMutation({ mutationFn: api.approveChange, onSuccess: refresh })
  const reject = useMutation({ mutationFn: ({ id, reason }: { id: number; reason: string }) => api.rejectChange(id, reason), onSuccess: refresh })
  const rows = pending.data ?? []
  return (
    <Card title={`Grade change requests (${rows.length} pending)`}>
      {rows.length === 0 ? <Empty>No pending requests.</Empty> : (
        <Table head={['Student', 'Subject', 'Change', 'Reason', '']}>
          {rows.map((c) => (
            <tr key={c.id}>
              <Td>{c.student_name}<span className="block text-xs text-muted">{c.student_number}</span></Td>
              <Td>{c.subject_code} ({c.section_code})<span className="block text-xs text-muted">{c.term}</span></Td>
              <Td className="num whitespace-nowrap">{c.old_value ?? '—'} → <span className="font-semibold">{c.new_value}</span></Td>
              <Td className="text-ink-2">{c.reason}<span className="block text-xs text-muted">by {c.requested_by_email ?? '—'}</span></Td>
              <Td><span className="flex flex-wrap gap-2">
                <Button onClick={() => approve.mutate(c.id)} busy={approve.isPending && approve.variables === c.id}>Approve</Button>
                <ReasonButton label="Reject" variant="danger" onConfirm={(reason) => reject.mutateAsync({ id: c.id, reason })} />
              </span></Td>
            </tr>
          ))}
        </Table>
      )}
      <ErrorText error={approve.error ?? reject.error} />
      <p className="mt-2 text-xs text-muted">Approving changes the official grade. Documents already issued keep what they printed; a later scan of one will ask for review.</p>
    </Card>
  )
}

export function Records() {
  return (
    <Page title="Records">
      <GradeChanges />
      <NewStudent />
      <ImportStudents />
      <Students />
    </Page>
  )
}
