import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { api, type IntakeSettings, type IntakeThresholds } from '../../api'
import { Button, Card, Empty, ErrorText, Field, Page } from '../../components/ui'
import { formatDate } from '../../lib/format'

type Key = keyof IntakeThresholds

const FIELDS: { key: Key; label: string; hint: string }[] = [
  { key: 'min_doc_type_confidence', label: 'Minimum document-type confidence', hint: 'Below this, the page needs manual review.' },
  { key: 'max_indicator_severity', label: 'Indicator severity that triggers review', hint: 'Any suspicious feature at or above this sends the page to Tray 2.' },
  { key: 'min_layout_score', label: 'Minimum layout match', hint: 'Below this, the page needs manual review.' },
  { key: 'min_similarity_score', label: 'Minimum similarity to reference samples', hint: 'Only applied when the station measured similarity.' },
]

function SettingsForm({ settings }: { settings: IntakeSettings }) {
  const qc = useQueryClient()
  const [values, setValues] = useState(() =>
    Object.fromEntries(FIELDS.map((f) => [f.key, String(settings[f.key])])) as Record<Key, string>)
  const [reason, setReason] = useState('')
  const save = useMutation({
    mutationFn: () => api.saveIntakeSettings({
      min_doc_type_confidence: Number(values.min_doc_type_confidence),
      max_indicator_severity: Number(values.max_indicator_severity),
      min_layout_score: Number(values.min_layout_score),
      min_similarity_score: Number(values.min_similarity_score),
      reason: reason.trim(),
    }),
    onSuccess: (data) => qc.setQueryData(['intake-settings'], data),
  })
  const onSubmit = (e: FormEvent) => { e.preventDefault(); save.mutate() }
  return (
    <form onSubmit={onSubmit} className="max-w-xl space-y-3" data-testid="intake-settings-form">
      {FIELDS.map((f) => (
        <Field key={f.key} label={f.label} type="number" step="0.01" min={0} max={1} required
          hint={`${f.hint} Default ${settings.defaults[f.key]}.`}
          value={values[f.key]} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
          data-testid={`intake-setting-${f.key}`} />
      ))}
      <Field label="Reason for the change (kept in the audit trail)" required minLength={3}
        value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button type="submit" busy={save.isPending} disabled={reason.trim().length < 3}>Save thresholds</Button>
      <ErrorText error={save.error} />
      {settings.updated_at && <p className="text-xs text-muted">Last changed {formatDate(settings.updated_at, true)}</p>}
    </form>
  )
}

/** Admin: thresholds of the incoming-document screening decision (values 0 to 1). */
export function IntakeSettingsPage() {
  const settings = useQuery({ queryKey: ['intake-settings'], queryFn: api.intakeSettings })
  return (
    <Page title="Screening thresholds">
      <Card title="Incoming-document screening">
        {settings.data
          ? <SettingsForm key={settings.data.updated_at ?? 'defaults'} settings={settings.data} />
          : <Empty>{settings.error ? (settings.error as Error).message : 'Loading…'}</Empty>}
      </Card>
    </Page>
  )
}
