import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type IntakeRow } from '../../api'
import { Card, Empty, ErrorText, Page, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { DOC_TYPE_LABEL } from '../../lib/intake'
import { DecisionBadge, IntakeStatusBadge } from './IntakeBadges'

function Profile({ id }: { id: number }) {
  const student = useQuery({ queryKey: ['student', id], queryFn: () => api.student(id) })
  if (student.isPending) return <Empty>Loading…</Empty>
  if (student.error) return <ErrorText error={student.error} />
  const s = student.data
  const rows: [string, string | number | null | undefined][] = [
    ['Student number', s.student_number],
    ['Name', [s.last_name + ',', s.first_name, s.middle_name, s.suffix].filter(Boolean).join(' ')],
    ['LRN', s.lrn], ['Sex', s.sex], ['Date of birth', s.birthdate], ['Place of birth', s.birth_place],
    ["Mother's maiden name", s.mother_name], ["Father's name", s.father_name],
    ['Year level', s.year_level], ['Status', s.status],
  ]
  return (
    <Card title="Profile">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid="student-profile">
        {rows.map(([k, v]) => (
          <div key={k} className="contents"><dt className="text-ink-2">{k}</dt><dd className="text-ink" data-field={k}>{v ?? '—'}</dd></div>
        ))}
      </dl>
    </Card>
  )
}

/** Enrollment documents linked to this student (S-07), each with its captured page. */
function EnrollmentDocuments({ id }: { id: number }) {
  const docs = useQuery({ queryKey: ['intake', { student_id: id }], queryFn: () => api.intakeQueue({ student_id: id, status: '' }) })
  const [open, setOpen] = useState<IntakeRow | null>(null)
  const rows = docs.data ?? []
  return (
    <Card title={`Enrollment documents (${rows.length})`}>
      <ErrorText error={docs.error} />
      {docs.isPending ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>No enrollment documents linked to this student.</Empty> : (
        <div className="space-y-4" data-testid="student-intake-docs">
          <Table head={['Page', 'Document', 'Screening', 'Status', 'Linked', '']}>
            {rows.map((r) => (
              <tr key={r.id} data-testid="student-intake-doc" data-intake-id={r.id}>
                <Td>
                  <button type="button" onClick={() => setOpen(open?.id === r.id ? null : r)} aria-expanded={open?.id === r.id}
                    data-testid="student-intake-thumb" aria-label={`View the page of document #${r.id}`}>
                    <img src={api.intakeImageUrl(r.id)} alt="" className="h-16 w-12 rounded border border-line bg-white object-cover" />
                  </button>
                </Td>
                <Td><Link to={`/intake/${r.id}`} className="text-accent hover:underline">#{r.id}</Link> {DOC_TYPE_LABEL[r.doc_type]}</Td>
                <Td><DecisionBadge decision={r.decision} /></Td>
                <Td><IntakeStatusBadge status={r.status} /></Td>
                <Td>{formatDate(r.linked_at, true)}</Td>
                <Td>
                  <button type="button" className="text-xs text-accent hover:underline" onClick={() => setOpen(open?.id === r.id ? null : r)}>
                    {open?.id === r.id ? 'Close' : 'View page'}
                  </button>
                </Td>
              </tr>
            ))}
          </Table>
          {open && (
            <figure className="rounded-md border border-line p-2" data-testid="student-intake-viewer">
              <figcaption className="mb-2 flex items-center justify-between text-sm text-ink-2">
                <span>#{open.id} · {DOC_TYPE_LABEL[open.doc_type]}</span>
                <a href={api.intakeImageUrl(open.id)} target="_blank" rel="noopener" className="text-accent hover:underline">Open full size</a>
              </figcaption>
              <img src={api.intakeImageUrl(open.id)} alt={`Captured page of enrollment document ${open.id}`}
                data-testid="student-intake-image" className="mx-auto max-h-[80vh] rounded-md border border-line bg-white" />
            </figure>
          )}
        </div>
      )}
    </Card>
  )
}

/** Registrar view of one student: profile and the enrollment documents linked to it. */
export function StudentRecord() {
  const id = Number(useParams().id)
  return (
    <Page title="Student record" actions={<Link to="/records" className="text-sm text-accent hover:underline">← Records</Link>}>
      <div className="grid gap-5 lg:grid-cols-[1fr_2fr]">
        <Profile id={id} />
        <EnrollmentDocuments id={id} />
      </div>
    </Page>
  )
}
