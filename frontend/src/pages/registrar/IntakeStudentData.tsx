import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiError, api, type IntakeCandidate, type IntakeDetail, type IntakeDocType, type IntakeFieldSpec, type IntakeFields,
} from '../../api'
import { ReasonButton } from '../../components/forms'
import { Button, Card, Empty, ErrorText, Field, Select } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { CONFLICT_LABEL, DOC_TYPE_LABEL, MATCH_LABEL, percent } from '../../lib/intake'

/** Below this the station was unsure: the field is highlighted for checking (a hint, not a rule). */
const LOW_CONFIDENCE = 0.8

/** Per-field messages from a 422 of PUT /fields: {detail: {message, errors: {key: text}}}. */
function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const detail = (error.detail as { detail?: { errors?: Record<string, string> } })?.detail
  return detail && typeof detail === 'object' && detail.errors ? detail.errors : {}
}

function initialValues(f: IntakeFields): Record<string, string> {
  const source = f.reviewed && f.schema_doc_type === f.doc_type
    ? f.reviewed.values
    : Object.fromEntries(Object.entries(f.extracted).map(([k, v]) => [k, v.value]))
  return Object.fromEntries(f.schema.map((s) => [s.key, source[s.key] ?? '']))
}

function StationHint({ spec, f }: { spec: IntakeFieldSpec; f: IntakeFields }) {
  const read = f.extracted[spec.key]
  if (!read || read.read_as === null) return <span className="mt-1 block text-xs text-muted">Not read by the station</span>
  const low = read.confidence !== null && read.confidence < LOW_CONFIDENCE
  return (
    <span className={`mt-1 block text-xs ${low ? 'font-semibold text-critical' : 'text-muted'}`}
      data-testid="field-confidence" data-low={low ? 'true' : 'false'}>
      {low && <span aria-hidden>! </span>}Station read “{read.read_as}” · {read.confidence === null ? 'confidence not given' : `${percent(read.confidence)} confident`}
      {read.derived && ' · split from the full name, check it'}
      {low && ' · check this'}
    </span>
  )
}

function FieldsForm({ d, f, docType, onDocType }: {
  d: IntakeDetail; f: IntakeFields; docType: IntakeDocType | undefined; onDocType: (t: IntakeDocType) => void
}) {
  const qc = useQueryClient()
  const [values, setValues] = useState(() => initialValues(f))
  const save = useMutation({
    mutationFn: () => api.saveIntakeFields(d.id,
      Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim() === '' ? null : v])),
      f.doc_type === 'unknown' || (docType && docType !== f.doc_type) ? f.schema_doc_type : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intake-fields', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-detail', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-candidates', d.id] })
      qc.invalidateQueries({ queryKey: ['intake'] })
    },
  })
  const errors = fieldErrors(save.error)
  const missing = f.schema.filter((s) => s.required && !values[s.key]?.trim()).map((s) => s.key)
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate() }

  return (
    <form onSubmit={submit} className="space-y-3" data-testid="student-data-form" noValidate>
      <Select label="Document type" value={f.schema_doc_type === 'unknown' ? '' : f.schema_doc_type}
        data-testid="student-data-doc-type" onChange={(e) => onDocType(e.target.value as IntakeDocType)}>
        {f.schema_doc_type === 'unknown' && <option value="">Choose the document type…</option>}
        {f.doc_types.map((t) => <option key={t} value={t}>{DOC_TYPE_LABEL[t]}</option>)}
      </Select>
      {f.schema.length === 0 ? <Empty>Choose the document type to see its fields.</Empty> : (
        <div className="grid gap-3 sm:grid-cols-2">
          {f.schema.map((spec) => {
            const error = errors[spec.key]
            return (
              <div key={spec.key} data-testid="student-field" data-key={spec.key} data-invalid={error ? 'true' : 'false'}>
                <Field label={`${spec.label}${spec.required ? ' *' : ''}`} name={spec.key}
                  type={spec.kind === 'date' ? 'date' : 'text'} inputMode={spec.kind === 'lrn' ? 'numeric' : undefined}
                  maxLength={spec.max_length} value={values[spec.key] ?? ''} aria-invalid={!!error}
                  data-testid={`field-${spec.key}`}
                  onChange={(e) => setValues({ ...values, [spec.key]: e.target.value })}
                  hint={spec.hint ?? undefined} />
                <StationHint spec={spec} f={f} />
                {error && <span role="alert" className="mt-1 block text-xs font-semibold text-critical" data-testid="field-error">{error}</span>}
                {spec.student_attr && <span className="block text-[11px] text-muted">Goes to the student profile</span>}
              </div>
            )
          })}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" busy={save.isPending} disabled={f.schema.length === 0 || missing.length > 0}
          data-testid="student-data-save">Save reviewed fields</Button>
        {missing.length > 0 && <span className="text-xs text-ink-2">Fill the required fields (*) first.</span>}
        {save.isSuccess && <span className="text-xs text-good-ink" data-testid="student-data-saved">✓ Reviewed fields saved.</span>}
      </div>
      {save.error && Object.keys(errors).length === 0 && <ErrorText error={save.error} />}
      {Object.keys(errors).length > 0 && <p role="alert" className="text-sm text-critical">Some fields need attention (see above).</p>}
      <p className="text-xs text-muted">
        The station's own reading is kept unchanged; what you save here is stored separately as the reviewed version.
        The audit trail records which fields you changed, not their values.
      </p>
    </form>
  )
}

function Candidate({ c, d }: { c: IntakeCandidate; d: IntakeDetail }) {
  const qc = useQueryClient()
  const link = useMutation({
    mutationFn: (reason?: string) => api.linkIntake(d.id, c.student_id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intake-fields', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-detail', d.id] })
      qc.invalidateQueries({ queryKey: ['intake'] })
    },
  })
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-line p-2" data-testid="candidate"
      data-student-id={c.student_id} data-strength={c.strength}>
      <div className="text-sm">
        <span className="font-medium text-ink">{c.name}</span> <span className="num text-ink-2">· {c.student_number}</span>
        <span className="block text-xs text-ink-2">
          {c.strength === 'strong' ? '✓ ' : '? '}{MATCH_LABEL[c.match] ?? c.match}
          {c.conflicts.length > 0 && <span className="text-critical"> · ! {c.conflicts.map((x) => CONFLICT_LABEL[x] ?? x).join(', ')}</span>}
        </span>
        <span className="block text-xs text-muted">Birth date {c.birthdate ?? '—'} · LRN {c.lrn ?? '—'}</span>
      </div>
      {c.strength === 'strong' ? (
        <Button onClick={() => link.mutate(undefined)} busy={link.isPending} data-testid="candidate-link">Link to this student</Button>
      ) : (
        <ReasonButton label="Link anyway…" confirmLabel="Link to this student" busy={link.isPending}
          hint="Not a strong match: say why this is the same student." onConfirm={(reason) => link.mutateAsync(reason)} />
      )}
      <ErrorText error={link.error} />
    </li>
  )
}

function CreateProfile({ d, hasCandidates }: { d: IntakeDetail; hasCandidates: boolean }) {
  const qc = useQueryClient()
  const programs = useQuery({ queryKey: ['programs'], queryFn: api.programs })
  const [form, setForm] = useState({ student_number: '', program_id: '', year_level: '1', confirm: false })
  const create = useMutation({
    mutationFn: () => api.createStudentFromIntake(d.id, {
      student_number: form.student_number.trim(), program_id: form.program_id ? Number(form.program_id) : null,
      year_level: form.year_level ? Number(form.year_level) : null, confirm_new: hasCandidates && form.confirm,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intake-fields', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-detail', d.id] })
      qc.invalidateQueries({ queryKey: ['intake'] })
      qc.invalidateQueries({ queryKey: ['students'] })
    },
  })
  const submit = (e: FormEvent) => { e.preventDefault(); create.mutate() }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-line p-3" data-testid="create-student-form">
      <p className="text-sm font-semibold text-ink">Create a new student profile</p>
      <p className="text-xs text-ink-2">Names, sex, birth date and place, parents and LRN come from the reviewed fields. Add what the document doesn't say.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Student number *" required value={form.student_number} data-testid="create-student-number"
          onChange={(e) => setForm({ ...form, student_number: e.target.value })} />
        <Select label="Program" value={form.program_id} onChange={(e) => setForm({ ...form, program_id: e.target.value })}>
          <option value="">—</option>
          {(programs.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.code}</option>)}
        </Select>
        <Field label="Year level" type="number" min={1} max={6} value={form.year_level}
          onChange={(e) => setForm({ ...form, year_level: e.target.value })} />
      </div>
      {hasCandidates && (
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={form.confirm} data-testid="create-student-confirm"
            onChange={(e) => setForm({ ...form, confirm: e.target.checked })} />
          None of the students above is this person
        </label>
      )}
      <Button type="submit" busy={create.isPending} data-testid="create-student"
        disabled={!form.student_number.trim() || (hasCandidates && !form.confirm)}>Create profile and link</Button>
      <ErrorText error={create.error} />
    </form>
  )
}

function MatchOrCreate({ d }: { d: IntakeDetail }) {
  const candidates = useQuery({ queryKey: ['intake-candidates', d.id], queryFn: () => api.intakeCandidates(d.id) })
  const rows = candidates.data ?? []
  return (
    <div className="space-y-3" data-testid="student-match">
      <p className="text-sm font-semibold text-ink">Link to a student</p>
      <p className="text-xs text-ink-2">Suggestions by LRN first, then name and birth date. Nothing is linked until you confirm.</p>
      <ErrorText error={candidates.error} />
      {candidates.isPending ? <Empty>Looking for matching students…</Empty> : rows.length === 0
        ? <p className="text-sm text-ink-2" data-testid="no-candidates">No existing student matches these fields.</p>
        : <ul className="space-y-2" data-testid="candidates">{rows.map((c) => <Candidate key={c.student_id} c={c} d={d} />)}</ul>}
      {!candidates.isPending && <CreateProfile d={d} hasCandidates={rows.length > 0} />}
    </div>
  )
}

function Linked({ d, f }: { d: IntakeDetail; f: IntakeFields }) {
  const qc = useQueryClient()
  const unlink = useMutation({
    mutationFn: (reason: string) => api.unlinkIntake(d.id, reason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intake-fields', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-detail', d.id] })
      qc.invalidateQueries({ queryKey: ['intake-candidates', d.id] })
    },
  })
  const s = f.student!
  return (
    <div className="space-y-2 rounded-md border border-good p-3" data-testid="student-linked" data-student-id={s.id}>
      <p className="text-sm text-ink">
        <span aria-hidden>✓ </span>Linked to <Link to={`/records/students/${s.id}`} className="font-medium text-accent hover:underline"
          data-testid="student-linked-record">{s.name} ({s.student_number})</Link>
      </p>
      <p className="text-xs text-ink-2">by {f.linked_by ?? '—'}, {formatDate(f.linked_at, true)}</p>
      <ReasonButton label="Unlink" variant="danger" busy={unlink.isPending} error={unlink.error}
        hint="The student profile is not changed; the document goes back to 'data reviewed'." onConfirm={(r) => unlink.mutateAsync(r)} />
    </div>
  )
}

/** S-06/S-07: review the fields the station read, then link the document to a student or create one. */
export function StudentData({ d }: { d: IntakeDetail }) {
  const [docType, setDocType] = useState<IntakeDocType | undefined>()
  const fields = useQuery({ queryKey: ['intake-fields', d.id, docType ?? null], queryFn: () => api.intakeFields(d.id, docType) })
  const f = fields.data

  let body
  if (fields.isPending) body = <Empty>Loading…</Empty>
  else if (fields.error || !f) body = <ErrorText error={fields.error} />
  else if (f.blocked) body = <p className="text-sm text-ink-2" data-testid="student-data-blocked">{f.blocked}</p>
  else body = (
    <div className="space-y-5">
      {f.status === 'linked' ? <Linked d={d} f={f} /> : (
        <FieldsForm key={f.schema_doc_type} d={d} f={f} docType={docType} onDocType={setDocType} />
      )}
      {f.status === 'data_reviewed' && <MatchOrCreate d={d} />}
      {f.status === 'linked' && f.reviewed && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm" data-testid="student-data-reviewed">
          {f.schema.map((s) => (
            <div key={s.key} className="contents"><dt className="text-ink-2">{s.label}</dt><dd className="text-ink">{f.reviewed!.values[s.key] ?? '—'}</dd></div>
          ))}
        </dl>
      )}
    </div>
  )

  return (
    <Card title="Student data (review the fields read from the page)">
      <div className="grid gap-5 lg:grid-cols-2" data-testid="student-data" data-status={f?.status}>
        <div>
          {d.has_image ? (
            <a href={api.intakeImageUrl(d.id)} target="_blank" rel="noopener">
              <img src={api.intakeImageUrl(d.id)} alt={`Page of incoming document ${d.id}, for checking the fields`}
                className="w-full rounded-md border border-line bg-white lg:sticky lg:top-4" />
            </a>
          ) : <Empty>No image stored.</Empty>}
        </div>
        <div>{body}</div>
      </div>
    </Card>
  )
}
