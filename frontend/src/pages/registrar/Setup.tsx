import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, ApiError, type AdviserRow, type SectionRow } from '../../api'
import { ReasonButton } from '../../components/forms'
import { Button, Card, Empty, ErrorText, Field, Page, Select, Table, Td } from '../../components/ui'

/** Section 5C: programs, subjects, terms, teachers, class sections and enrollments. */

function useRefresh(...keys: string[]) {
  const qc = useQueryClient()
  return () => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }))
}

function Programs() {
  const programs = useQuery({ queryKey: ['programs'], queryFn: api.programs })
  const [form, setForm] = useState({ code: '', name: '' })
  const refresh = useRefresh('programs')
  const add = useMutation({ mutationFn: () => api.createProgram(form), onSuccess: () => { setForm({ code: '', name: '' }); refresh() } })
  return (
    <Card title="Programs">
      <form onSubmit={(e) => { e.preventDefault(); add.mutate() }} className="mb-3 grid gap-2 sm:grid-cols-[1fr_3fr_auto] sm:items-end">
        <Field label="Code" required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="BSIT" />
        <Field label="Name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Button type="submit" busy={add.isPending}>Add</Button>
      </form>
      <ErrorText error={add.error} />
      <Table head={['Code', 'Name']}>
        {(programs.data ?? []).map((p) => <tr key={p.id}><Td>{p.code}</Td><Td>{p.name}</Td></tr>)}
      </Table>
    </Card>
  )
}

function Subjects() {
  const subjects = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const programs = useQuery({ queryKey: ['programs'], queryFn: api.programs })
  const empty = { code: '', title: '', units: '3', program_id: '' }
  const [form, setForm] = useState(empty)
  const refresh = useRefresh('subjects')
  const add = useMutation({
    mutationFn: () => api.createSubject({ code: form.code, title: form.title, units: Number(form.units), program_id: form.program_id ? Number(form.program_id) : null }),
    onSuccess: () => { setForm(empty); refresh() },
  })
  const programCode = (id: number | null) => programs.data?.find((p) => p.id === id)?.code ?? 'General'
  return (
    <Card title="Subjects">
      <form onSubmit={(e) => { e.preventDefault(); add.mutate() }} className="mb-3 grid gap-2 sm:grid-cols-[1fr_3fr_1fr_1fr_auto] sm:items-end">
        <Field label="Code" required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} placeholder="IT101" />
        <Field label="Title" required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <Field label="Units" type="number" min={0.5} max={10} step={0.5} required value={form.units} onChange={(e) => setForm({ ...form, units: e.target.value })} />
        <Select label="Program" value={form.program_id} onChange={(e) => setForm({ ...form, program_id: e.target.value })}>
          <option value="">General</option>
          {(programs.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
        </Select>
        <Button type="submit" busy={add.isPending}>Add</Button>
      </form>
      <ErrorText error={add.error} />
      <div className="max-h-80 overflow-y-auto">
        <Table head={['Code', 'Title', 'Units', 'Program']}>
          {(subjects.data ?? []).map((s) => (
            <tr key={s.id}><Td>{s.code}</Td><Td>{s.title}</Td><Td className="num">{Number(s.units)}</Td><Td>{programCode(s.program_id)}</Td></tr>
          ))}
        </Table>
      </div>
    </Card>
  )
}

function Terms() {
  const terms = useQuery({ queryKey: ['terms'], queryFn: api.terms })
  const refresh = useRefresh('terms', 'sections-all')
  const [form, setForm] = useState({ academic_year: '', semester: '1st' })
  const add = useMutation({ mutationFn: () => api.createTerm(form.academic_year, form.semester), onSuccess: () => { setForm({ ...form, academic_year: '' }); refresh() } })
  const current = useMutation({ mutationFn: api.setCurrentTerm, onSuccess: refresh })
  const lock = useMutation({ mutationFn: ({ id, force }: { id: number; force: boolean }) => api.lockTerm(id, force), onSuccess: refresh })
  // "Lock anyway" only for the one warning it answers: enrolled students without a grade.
  const missingGrades = lock.error instanceof ApiError &&
    (lock.error.detail as { detail?: { code?: string } })?.detail?.code === 'missing_grades'
  return (
    <Card title="Terms and grading periods">
      <form onSubmit={(e) => { e.preventDefault(); add.mutate() }} className="mb-3 grid gap-2 sm:grid-cols-[2fr_1fr_auto] sm:items-end">
        <Field label="Academic year" required pattern="\d{4}-\d{4}" placeholder="2026-2027" value={form.academic_year}
          onChange={(e) => setForm({ ...form, academic_year: e.target.value })} />
        <Select label="Semester" value={form.semester} onChange={(e) => setForm({ ...form, semester: e.target.value })}>
          <option value="1st">1st</option><option value="2nd">2nd</option><option value="Summer">Summer</option>
        </Select>
        <Button type="submit" busy={add.isPending}>Add term</Button>
      </form>
      <ErrorText error={add.error ?? current.error} />
      <p className="mb-2 text-xs text-muted">Locking makes a term's grades final. After that, changes go through registrar-approved grade change requests.</p>
      <Table head={['Term', 'Status', '']}>
        {(terms.data ?? []).map((t) => (
          <tr key={t.id}>
            <Td>{t.semester} Semester, AY {t.academic_year}{t.is_current && <span className="ml-2 text-xs font-semibold text-ink-2">current</span>}</Td>
            <Td>{t.is_grades_locked ? '🔒 Locked' : 'Open for encoding'}</Td>
            <Td><span className="flex flex-wrap gap-2">
              {!t.is_current && <Button variant="secondary" onClick={() => current.mutate(t.id)}>Make current</Button>}
              {!t.is_grades_locked && <Button variant="secondary" onClick={() => lock.mutate({ id: t.id, force: false })}
                busy={lock.isPending && lock.variables?.id === t.id}>Lock grades</Button>}
              {!t.is_grades_locked && missingGrades && lock.variables?.id === t.id && (
                <Button variant="danger" onClick={() => lock.mutate({ id: t.id, force: true })}>Lock anyway</Button>
              )}
            </span></Td>
          </tr>
        ))}
      </Table>
      <ErrorText error={lock.error} />
    </Card>
  )
}

function TeacherReset({ userId }: { userId: number }) {
  const reset = useMutation({ mutationFn: ({ pw, reason }: { pw: string; reason: string }) => api.resetPassword(userId, pw, reason) })
  if (reset.isSuccess) return <span className="text-xs text-good-ink">✓ Password reset</span>
  return (
    <ReasonButton label="Reset password" extraType="password" extraLabel="New temporary password (10+ characters)"
      hint="Also unlocks the account. The teacher must change it at next login." busy={reset.isPending} error={reset.error}
      onConfirm={(reason, pw) => reset.mutateAsync({ pw, reason })} />
  )
}

function Teachers() {
  const teachers = useQuery({ queryKey: ['teachers'], queryFn: api.teachers })
  const empty = { employee_number: '', full_name: '', department: '', email: '', temporary_password: '' }
  const [form, setForm] = useState(empty)
  const refresh = useRefresh('teachers')
  const add = useMutation({
    mutationFn: () => api.createTeacher({
      employee_number: form.employee_number, full_name: form.full_name, department: form.department || null,
      ...(form.email ? { email: form.email, temporary_password: form.temporary_password } : {}),
    }),
    onSuccess: () => { setForm(empty); refresh() },
  })
  const set = (k: keyof typeof empty) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  return (
    <Card title="Teachers">
      <form onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate() }} className="mb-3 grid gap-2 sm:grid-cols-3">
        <Field label="Employee number" required value={form.employee_number} onChange={set('employee_number')} />
        <Field label="Full name" required value={form.full_name} onChange={set('full_name')} />
        <Field label="Department" value={form.department} onChange={set('department')} />
        <Field label="Login email (optional)" type="email" value={form.email} onChange={set('email')} />
        <Field label="Temporary password" type="password" minLength={10} required={!!form.email} value={form.temporary_password}
          onChange={set('temporary_password')} hint="Must change on first login" />
        <div className="sm:self-end"><Button type="submit" busy={add.isPending}>Add teacher</Button></div>
      </form>
      <ErrorText error={add.error} />
      <Table head={['Employee no.', 'Name', 'Department', 'Login', '']}>
        {(teachers.data ?? []).map((t) => (
          <tr key={t.id}><Td>{t.employee_number}</Td><Td>{t.full_name}</Td><Td>{t.department ?? '—'}</Td><Td>{t.user_id ? 'Yes' : 'No'}</Td>
            <Td>{t.user_id && <TeacherReset userId={t.user_id} />}</Td></tr>
        ))}
      </Table>
    </Card>
  )
}

function Enroll({ section }: { section: SectionRow }) {
  const [open, setOpen] = useState(false)
  const [numbers, setNumbers] = useState('')
  const enroll = useMutation({
    mutationFn: () => api.enrollByNumber(section.id, numbers.split(/[\s,;]+/).filter(Boolean)),
    onSuccess: () => setNumbers(''),
  })
  if (section.is_grades_locked) return null
  if (!open) return <button onClick={() => setOpen(true)} className="text-xs text-accent hover:underline">Enroll students</button>
  return (
    <div className="mt-2 space-y-2">
      <label className="block text-xs text-ink-2">Student numbers (one per line, or separated by commas)
        <textarea rows={4} value={numbers} onChange={(e) => setNumbers(e.target.value)}
          className="mt-1 w-full rounded-md border border-line bg-surface px-2 py-1 font-mono text-xs text-ink" />
      </label>
      <div className="flex gap-2">
        <Button disabled={!numbers.trim()} busy={enroll.isPending} onClick={() => enroll.mutate()}>Enroll</Button>
        <Button variant="secondary" onClick={() => setOpen(false)}>Close</Button>
      </div>
      {enroll.data && <p className="text-xs text-good-ink">✓ {enroll.data.enrolled} student(s) enrolled.</p>}
      <ErrorText error={enroll.error} />
    </div>
  )
}

function Sections() {
  const sections = useQuery({ queryKey: ['sections-all'], queryFn: api.allSections })
  const subjects = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const terms = useQuery({ queryKey: ['terms'], queryFn: api.terms })
  const teachers = useQuery({ queryKey: ['teachers'], queryFn: api.teachers })
  const [termId, setTermId] = useState('')
  const empty = { subject_id: '', section_code: '', teacher_id: '' }
  const [form, setForm] = useState(empty)
  const refresh = useRefresh('sections-all', 'sections', 'advisers')
  const add = useMutation({
    mutationFn: () => api.createSection({ subject_id: Number(form.subject_id), term_id: Number(termId), section_code: form.section_code, teacher_id: form.teacher_id ? Number(form.teacher_id) : null }),
    onSuccess: () => { setForm(empty); refresh() },
  })
  const assign = useMutation({ mutationFn: ({ id, teacher }: { id: number; teacher: number | null }) => api.assignTeacher(id, teacher), onSuccess: refresh })
  const shown = (sections.data ?? []).filter((s) => !termId || s.term_id === Number(termId))
  return (
    <Card title="Class sections" actions={
      <Select label="" aria-label="Term" value={termId} onChange={(e) => setTermId(e.target.value)}>
        <option value="">All terms</option>
        {(terms.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.semester} Sem, AY {t.academic_year}</option>)}
      </Select>
    }>
      {termId ? (
        <form onSubmit={(e) => { e.preventDefault(); add.mutate() }} className="mb-3 grid gap-2 sm:grid-cols-[2fr_1fr_2fr_auto] sm:items-end">
          <Select label="Subject" required value={form.subject_id} onChange={(e) => setForm({ ...form, subject_id: e.target.value })}>
            <option value="">Choose…</option>
            {(subjects.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.code} · {s.title}</option>)}
          </Select>
          <Field label="Section" required placeholder="BSIT-1A" value={form.section_code} onChange={(e) => setForm({ ...form, section_code: e.target.value })} />
          <Select label="Teacher" value={form.teacher_id} onChange={(e) => setForm({ ...form, teacher_id: e.target.value })}>
            <option value="">Not assigned</option>
            {(teachers.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.full_name}</option>)}
          </Select>
          <Button type="submit" busy={add.isPending}>Add section</Button>
        </form>
      ) : <p className="mb-3 text-xs text-muted">Choose a term to add sections to it.</p>}
      <ErrorText error={add.error ?? assign.error} />
      {sections.isPending ? <Empty>Loading…</Empty> : shown.length === 0 ? <Empty>No sections.</Empty> : (
        <Table head={['Subject', 'Section', 'Term', 'Teacher']}>
          {shown.map((s) => (
            <tr key={s.id}>
              <Td>{s.subject_code}<span className="block text-xs text-muted">{s.subject_title}</span><Enroll section={s} /></Td>
              <Td>{s.section_code}</Td>
              <Td className="whitespace-nowrap">{s.semester} {s.academic_year}{s.is_grades_locked ? ' 🔒' : ''}</Td>
              <Td>
                <select aria-label={`Teacher for ${s.subject_code} ${s.section_code}`} value={s.teacher_id ?? ''}
                  onChange={(e) => assign.mutate({ id: s.id, teacher: e.target.value ? Number(e.target.value) : null })}
                  className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink">
                  <option value="">Not assigned</option>
                  {(teachers.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.full_name}</option>)}
                </select>
              </Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}

/** S-08: one class adviser per block section (all subjects of a term sharing a section code). */
function Advisers() {
  const terms = useQuery({ queryKey: ['terms'], queryFn: api.terms })
  const teachers = useQuery({ queryKey: ['teachers'], queryFn: api.teachers })
  const [termId, setTermId] = useState('')
  const blocks = useQuery({ queryKey: ['advisers', termId], queryFn: () => api.sectionAdvisers(termId ? Number(termId) : undefined) })
  const refresh = useRefresh('advisers')
  const assign = useMutation({
    mutationFn: ({ b, teacher }: { b: AdviserRow; teacher: number | null }) =>
      api.setAdviser({ term_id: b.term_id, section_code: b.section_code, teacher_id: teacher }),
    onSuccess: refresh,
  })
  const rows = blocks.data ?? []
  return (
    <Card title="Class advisers" actions={
      <Select label="" aria-label="Adviser term" value={termId} onChange={(e) => setTermId(e.target.value)}>
        <option value="">All terms</option>
        {(terms.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.semester} Sem, AY {t.academic_year}</option>)}
      </Select>
    }>
      <p className="mb-2 text-xs text-muted">The adviser sees the section's students and can view (not edit) every subject's grades. Changes are recorded in the audit trail.</p>
      <ErrorText error={assign.error} />
      {blocks.isPending ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No sections yet. Add class sections first.</Empty> : (
        <Table head={['Section', 'Term', 'Subjects', 'Students', 'Adviser']}>
          {rows.map((b) => (
            <tr key={`${b.term_id}-${b.section_code}`}>
              <Td>{b.section_code}</Td>
              <Td className="whitespace-nowrap">{b.semester} {b.academic_year}</Td>
              <Td className="num">{b.subjects}</Td><Td className="num">{b.students}</Td>
              <Td>
                <select aria-label={`Adviser for ${b.section_code} ${b.semester} ${b.academic_year}`} value={b.teacher_id ?? ''}
                  onChange={(e) => assign.mutate({ b, teacher: e.target.value ? Number(e.target.value) : null })}
                  className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink">
                  <option value="">No adviser</option>
                  {(teachers.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.full_name}</option>)}
                </select>
              </Td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  )
}

export function Setup() {
  return (
    <Page title="Academic setup">
      <Terms />
      <Sections />
      <Advisers />
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <Programs />
        <Teachers />
      </div>
      <Subjects />
    </Page>
  )
}
