import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, ApiError, type AdvisoryClass, type GradeRow, type Section } from '../../api'
import { ImportForm, ReasonButton } from '../../components/forms'
import { Button, Card, Empty, ErrorText, Page, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'

const GRADE_HINT = 'Scale: 1.00 to 3.00 in steps of 0.25, or 5.00, INC, DRP'

/** After the term is locked: ask the registrar to change one grade (Section 5C). */
function RequestChange({ row }: { row: GradeRow }) {
  const qc = useQueryClient()
  const ask = useMutation({
    mutationFn: ({ grade, reason }: { grade: string; reason: string }) => api.requestGradeChange(row.grade_id!, grade, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['my-grade-changes'] }),
  })
  if (row.grade_id === null) return null
  if (ask.isSuccess) return <span className="text-xs text-good-ink">✓ Sent to the registrar</span>
  return (
    <ReasonButton label="Request change" confirmLabel="Send request" extraLabel={`Correct grade (${GRADE_HINT})`}
      busy={ask.isPending} error={ask.error} onConfirm={(reason, grade) => ask.mutateAsync({ grade, reason })} />
  )
}

function GradeSheet({ section }: { section: Section }) {
  const qc = useQueryClient()
  const roster = useQuery({ queryKey: ['grades', section.id], queryFn: () => api.sectionGrades(section.id) })
  const [edits, setEdits] = useState<Record<number, string>>({})
  const locked = section.is_grades_locked
  // Advisory sections the teacher does not teach are view-only (S-08).
  const viewOnly = !section.teaching
  const changed = Object.entries(edits).filter(([id, v]) =>
    v.trim() !== '' && v !== roster.data?.find((r) => r.enrollment_id === Number(id))?.grade)
  const refresh = () => qc.invalidateQueries({ queryKey: ['grades', section.id] })

  const save = useMutation({
    mutationFn: () => api.saveGrades(section.id, changed.map(([id, grade]) => ({ enrollment_id: Number(id), grade }))),
    onSuccess: () => { setEdits({}); refresh() },
  })
  const submit = useMutation({
    mutationFn: () => api.submitSection(section.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sections'] }),
  })
  const upload = useMutation({ mutationFn: (file: File) => api.importGrades(section.id, file), onSuccess: () => { setEdits({}); refresh() } })
  const saveReport = save.error instanceof ApiError
    ? (save.error.detail as { detail?: string; errors?: { enrollment_id: number; error: string }[] }) : null
  const rowErrors = saveReport?.errors ?? []

  return (
    <Card title={`${section.subject_code} · ${section.subject_title} (${section.section_code})`} actions={
      <span className="text-xs text-muted">{section.semester} Sem AY {section.academic_year}
        {locked ? ' · 🔒 locked' : section.grades_submitted ? ' · submitted' : ''}</span>
    }>
      {viewOnly && <p className="mb-2 text-sm text-ink-2">Advisory class: view only. The subject teacher encodes these grades.</p>}
      {locked && !viewOnly && <p className="mb-2 text-sm text-ink-2">This term is locked. To correct a grade, use “Request change”; the registrar approves it.</p>}
      {roster.isPending ? <Empty>Loading…</Empty> : (
        <Table head={['Student number', 'Name', 'Grade', 'Remarks']}>
          {roster.data!.map((r) => {
            const err = rowErrors.find((e) => e.enrollment_id === r.enrollment_id)
            return (
              <tr key={r.enrollment_id}>
                <Td className="num">{r.student_number}</Td><Td>{r.student_name}</Td>
                <Td>
                  {locked || viewOnly ? <span className="num">{r.grade ?? '—'}</span> : (
                    <input aria-label={`Grade for ${r.student_name}`} aria-invalid={!!err}
                      value={edits[r.enrollment_id] ?? r.grade ?? ''} placeholder="e.g. 1.75"
                      onChange={(e) => setEdits({ ...edits, [r.enrollment_id]: e.target.value })}
                      className={`num w-28 rounded-md border bg-surface px-2 py-1 text-sm text-ink ${err ? 'border-critical' : 'border-line'}`} />
                  )}
                  {err && <span className="mt-1 block text-xs text-critical">{err.error}</span>}
                </Td>
                <Td className="text-ink-2">{r.remarks ?? ''}{locked && !viewOnly && <div className="mt-1"><RequestChange row={r} /></div>}</Td>
              </tr>
            )
          })}
        </Table>
      )}
      {!locked && !viewOnly && (
        <>
          <p className="mt-2 text-xs text-muted">{GRADE_HINT}.</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button onClick={() => save.mutate()} disabled={changed.length === 0} busy={save.isPending}>
              Save {changed.length > 0 ? `${changed.length} change(s)` : ''}
            </Button>
            <Button variant="secondary" onClick={() => submit.mutate()} busy={submit.isPending}
              disabled={changed.length > 0 || section.grades_submitted}>Submit grades as final</Button>
          </div>
          {rowErrors.length > 0 && <p role="alert" className="mt-2 text-sm text-critical">✕ {saveReport?.detail}</p>}
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-accent">Upload grades from a file instead</summary>
            <div className="mt-2">
              <p className="mb-2 text-xs text-muted">A CSV or Excel file with columns student_number and grade. If any row has a problem, nothing is saved.</p>
              <ImportForm label="Grade file" onUpload={(f) => upload.mutate(f)} busy={upload.isPending} error={upload.error}
                done={upload.data && <p className="text-sm text-good-ink">✓ {upload.data.saved} grade(s) saved from the file.</p>} />
            </div>
          </details>
        </>
      )}
      {save.isSuccess && <p className="mt-2 text-sm text-good-ink">✓ Saved {save.data.saved} grade(s).</p>}
      {submit.isSuccess && <p className="mt-2 text-sm text-good-ink">✓ Submitted. The registrar will lock the term.</p>}
      <ErrorText error={rowErrors.length ? null : save.error} />
      <ErrorText error={submit.error} />
    </Card>
  )
}

function MyChangeRequests() {
  const mine = useQuery({ queryKey: ['my-grade-changes'], queryFn: () => api.changeRequests() })
  const rows = mine.data ?? []
  if (rows.length === 0) return null
  return (
    <Card title="My grade change requests">
      <Table head={['Student', 'Subject', 'Change', 'Status', 'Sent']}>
        {rows.map((c) => (
          <tr key={c.id}>
            <Td>{c.student_name}</Td><Td>{c.subject_code} ({c.section_code})</Td>
            <Td className="num">{c.old_value ?? '—'} → {c.new_value}</Td>
            <Td>{c.status}</Td><Td>{formatDate(c.created_at)}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  )
}

/** S-08: the block section(s) this teacher advises: subjects and enrolled students. */
function Advisory({ classes, selected, onSelect }: { classes: AdvisoryClass[]; selected: number | null; onSelect: (id: number) => void }) {
  if (classes.length === 0) return null
  return (
    <>
      {classes.map((c) => (
        <Card key={`${c.term_id}-${c.section_code}`} title={`Advisory class · ${c.section_code}`} actions={
          <span className="text-xs text-muted">{c.semester} Sem AY {c.academic_year}{c.is_grades_locked ? ' · 🔒 locked' : ''}</span>
        }>
          <p className="mb-2 text-xs text-muted">You are the class adviser. Open a subject to view its grades (view only).</p>
          <div className="mb-3 flex flex-wrap gap-2">
            {c.sections.map((s) => (
              <button key={s.id} onClick={() => onSelect(s.id)} aria-pressed={s.id === selected}
                className={`rounded-md border px-3 py-1.5 text-sm ${s.id === selected ? 'border-accent bg-surface-2 text-ink' : 'border-line text-ink-2'}`}>
                {s.subject_code}{s.teacher_name ? ` · ${s.teacher_name}` : ''}
              </button>
            ))}
          </div>
          {c.students.length === 0 ? <Empty>No students enrolled yet.</Empty> : (
            <Table head={['Student number', 'Name', 'Program', 'Year']}>
              {c.students.map((st) => (
                <tr key={st.student_id}>
                  <Td className="num">{st.student_number}</Td><Td>{st.student_name}</Td>
                  <Td>{st.program_code ?? '—'}</Td><Td className="num">{st.year_level ?? '—'}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      ))}
    </>
  )
}

export function TeacherHome() {
  const sections = useQuery({ queryKey: ['sections'], queryFn: api.sections })
  const advisory = useQuery({ queryKey: ['advisory'], queryFn: api.advisory })
  const [selected, setSelected] = useState<number | null>(null)
  const all = sections.data ?? []
  const list = all.filter((s) => s.teaching)
  const current = all.find((s) => s.id === selected)
  return (
    <Page title="My classes">
      <Card title="Class sections">
        {sections.isPending ? <Empty>Loading…</Empty> : list.length === 0 ? <Empty>No classes assigned.</Empty> : (
          <div className="flex flex-wrap gap-2">
            {list.map((s) => (
              <button key={s.id} onClick={() => setSelected(s.id)} aria-pressed={s.id === selected}
                className={`rounded-md border px-3 py-1.5 text-sm ${s.id === selected ? 'border-accent bg-surface-2 text-ink' : 'border-line text-ink-2'}`}>
                {s.subject_code} · {s.semester} {s.academic_year}{s.is_grades_locked ? ' 🔒' : ''}
              </button>
            ))}
          </div>
        )}
      </Card>
      <Advisory classes={advisory.data ?? []} selected={selected} onSelect={setSelected} />
      {current && <GradeSheet key={current.id} section={current} />}
      <MyChangeRequests />
    </Page>
  )
}
