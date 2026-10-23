import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, type User } from '../api'
import { Button, Card, Empty, ErrorText, Field, Page, Select, Table, Td } from '../components/ui'
import { formatDate } from '../lib/format'

// Per-student advisers (docs/applicant-intake.md, "Advisers"): one teacher-adviser per student
// and term. Separate from the class advisers of block sections (Academic setup, S-08).

function AssignAdviser() {
  const qc = useQueryClient()
  const options = useQuery({ queryKey: ['adviser-options'], queryFn: api.adviserOptions })
  const [studentId, setStudentId] = useState('')
  const [termId, setTermId] = useState('')
  const [teacherId, setTeacherId] = useState('')
  const [reason, setReason] = useState('')
  const terms = options.data?.terms ?? []
  const term = termId || String(terms.find((t) => t.is_current)?.id ?? '')
  const assign = useMutation({
    mutationFn: () => api.setStudentAdviser({
      student_id: Number(studentId), term_id: Number(term),
      teacher_id: teacherId ? Number(teacherId) : null, reason,
    }),
    onSuccess: () => { setReason(''); qc.invalidateQueries({ queryKey: ['student-advisers'] }) },
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); assign.mutate() }
  return (
    <Card title="Assign a student's adviser">
      <form onSubmit={onSubmit} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_2fr_2fr_auto] lg:items-end" data-testid="adviser-form">
        <Select label="Student" required value={studentId} onChange={(e) => setStudentId(e.target.value)}>
          <option value="">Choose…</option>
          {options.data?.students.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.student_number})</option>)}
        </Select>
        <Select label="Term" required value={term} onChange={(e) => setTermId(e.target.value)}>
          <option value="">Choose…</option>
          {terms.map((t) => <option key={t.id} value={t.id}>{t.name}{t.is_current ? ' (current)' : ''}</option>)}
        </Select>
        <Select label="Adviser" value={teacherId} onChange={(e) => setTeacherId(e.target.value)}>
          <option value="">No adviser (remove)</option>
          {options.data?.teachers.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </Select>
        <Field label="Reason (kept in the audit trail)" required minLength={3} maxLength={500} value={reason}
          onChange={(e) => setReason(e.target.value)} />
        <Button type="submit" busy={assign.isPending} disabled={!studentId || !term || reason.trim().length < 3}>Save</Button>
      </form>
      <ErrorText error={assign.error ?? options.error} />
    </Card>
  )
}

function StaffAdvisers() {
  const list = useQuery({ queryKey: ['student-advisers'], queryFn: () => api.studentAdvisers() })
  const rows = list.data ?? []
  return (
    <Page title="Advisers">
      <p className="text-sm text-ink-2">
        One teacher-adviser per student and term. Students see their own adviser; teachers see only the students
        assigned to them. Class advisers of block sections stay under Academic setup.
      </p>
      <AssignAdviser />
      <Card title="Assigned advisers">
        <ErrorText error={list.error} />
        {list.isPending ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No advisers assigned yet.</Empty> : (
          <Table head={['Term', 'Student', 'Adviser', 'Since']}>
            {rows.map((r) => (
              <tr key={r.id} data-testid="student-adviser-row">
                <Td>{r.term}</Td>
                <Td>{r.student_name}<span className="block text-xs text-muted">{r.student_number}</span></Td>
                <Td>{r.teacher_name}</Td>
                <Td>{formatDate(r.assigned_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  )
}

function MyAdviser() {
  const mine = useQuery({ queryKey: ['my-advisers'], queryFn: api.myAdvisers })
  return (
    <Page title="My adviser">
      <Card>
        <ErrorText error={mine.error} />
        {mine.isPending ? <Empty>Loading…</Empty> : (mine.data ?? []).length === 0 ? <Empty>No adviser assigned yet.</Empty> : (
          <Table head={['Term', 'Adviser', 'Department']}>
            {mine.data!.map((a) => (
              <tr key={a.term_id} data-testid="my-adviser-row">
                <Td>{a.term}{a.is_current && <span className="ml-1 text-xs text-muted">(current)</span>}</Td>
                <Td>{a.teacher_name}</Td>
                <Td>{a.department ?? '—'}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  )
}

function MyAdvisees() {
  const advisees = useQuery({ queryKey: ['advisees'], queryFn: api.advisees })
  return (
    <Page title="My advisees">
      <Card>
        <ErrorText error={advisees.error} />
        {advisees.isPending ? <Empty>Loading…</Empty> : (advisees.data ?? []).length === 0 ? <Empty>No students assigned to you.</Empty> : (
          <Table head={['Term', 'Student', 'Program', 'Year']}>
            {advisees.data!.map((a) => (
              <tr key={`${a.term_id}-${a.student_id}`} data-testid="advisee-row">
                <Td>{a.term}{a.is_current && <span className="ml-1 text-xs text-muted">(current)</span>}</Td>
                <Td>{a.student_name}<span className="block text-xs text-muted">{a.student_number}</span></Td>
                <Td>{a.program_code ?? '—'}</Td>
                <Td>{a.year_level ?? '—'}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </Page>
  )
}

export function Advisers({ user }: { user: User }) {
  if (user.role === 'student') return <MyAdviser />
  if (user.role === 'teacher') return <MyAdvisees />
  return <StaffAdvisers />
}
