import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type DocType, type Template, type Validation } from '../../api'
import { Button, Card, Empty, ErrorText, Page, Select, Table, Td, TextArea } from '../../components/ui'
import { formatDate } from '../../lib/format'

function Checks({ v }: { v: Validation }) {
  const problems = [
    ...v.errors,
    ...v.unknown_placeholders.map((p) => `Unknown placeholder: ${p}`),
    ...v.missing_placeholders.map((p) => `Missing placeholder: ${p}`),
    ...v.missing_zones.map((z) => `Missing OCR zone: ${z}`),
  ]
  if (problems.length === 0) return <span className="text-xs font-semibold text-good-ink">✓ All checks pass</span>
  return <ul className="list-inside list-disc text-xs text-critical">{problems.map((p) => <li key={p}>{p}</li>)}</ul>
}

function Editor({ base, onDone }: { base: Template; onDone: () => void }) {
  const qc = useQueryClient()
  const [html, setHtml] = useState(base.html_content)
  const [zones, setZones] = useState(JSON.stringify(base.field_zones, null, 2))
  const [draft, setDraft] = useState<Template | null>(base.status === 'draft' ? base : null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  useEffect(() => () => { if (pdfUrl) URL.revokeObjectURL(pdfUrl) }, [pdfUrl])

  const parsedZones = () => JSON.parse(zones)
  const save = useMutation({
    mutationFn: () => draft
      ? api.updateTemplate(draft.id, { html_content: html, field_zones: parsedZones() })
      : api.uploadTemplate({ document_type: base.document_type, html_content: html, field_zones: parsedZones() }),
    onSuccess: (t) => { setDraft(t); qc.invalidateQueries({ queryKey: ['templates'] }) },
  })
  const preview = useMutation({
    mutationFn: () => api.previewTemplate(draft!.id),
    onSuccess: (blob) => setPdfUrl(URL.createObjectURL(blob)),
  })
  const activate = useMutation({
    mutationFn: () => api.activateTemplate(draft!.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['templates'] }); onDone() },
  })

  return (
    <Card title={draft ? `Draft ${draft.document_type} v${draft.version}` : `New ${base.document_type} version (from v${base.version})`}
      actions={<button onClick={onDone} className="text-xs text-accent hover:underline">Close</button>}>
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <label className="block text-sm text-ink-2">Load HTML template file
            <input type="file" accept=".html,.htm,text/html" className="mt-1 block w-full text-sm"
              onChange={async (e) => { const file = e.target.files?.[0]; if (file) setHtml(await file.text()) }} />
          </label>
          <TextArea label="HTML template" rows={18} value={html} onChange={(e) => setHtml(e.target.value)} />
          <TextArea label="OCR field zones (mm on A4)" rows={8} value={zones} onChange={(e) => setZones(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => save.mutate()} busy={save.isPending}>{draft ? 'Save draft' : 'Save as new draft'}</Button>
            <Button variant="secondary" disabled={!draft} onClick={() => preview.mutate()} busy={preview.isPending}>Preview PDF</Button>
            <Button variant="secondary" disabled={!draft?.validation.can_activate} onClick={() => activate.mutate()} busy={activate.isPending}>Activate</Button>
          </div>
          {draft && <Checks v={draft.validation} />}
          <ErrorText error={save.error ?? preview.error ?? activate.error} />
        </div>
        <div>
          {pdfUrl ? <iframe title="Template preview" src={pdfUrl} className="h-[70vh] w-full rounded-md border border-line bg-white" />
            : <Empty>Save the draft, then preview it with sample data.</Empty>}
        </div>
      </div>
    </Card>
  )
}

export function Templates() {
  const templates = useQuery({ queryKey: ['templates'], queryFn: api.templates })
  const [editing, setEditing] = useState<Template | null>(null)
  const [type, setType] = useState<DocType>('TOR')
  const list = (templates.data ?? []).filter((t) => t.document_type === type)
  const active = list.find((t) => t.status === 'active')
  const base = active ?? [...list].sort((x, y) => y.version - x.version)[0]

  return (
    <Page title="Document templates">
      {editing ? <Editor key={editing.id} base={editing} onDone={() => setEditing(null)} /> : (
        <Card title="Versions" actions={
          <div className="flex items-center gap-2">
            <Select label="" aria-label="Document type" value={type} onChange={(e) => setType(e.target.value as DocType)}>
              <option value="TOR">TOR</option><option value="COE">COE</option>
            </Select>
            <Button onClick={() => setEditing(base ?? {
              id: 0, document_type: type, version: 0, status: 'retired', html_content: '',
              field_zones: null, created_at: '',
              validation: { placeholders: [], unknown_placeholders: [], missing_placeholders: [], missing_zones: [], errors: [], can_activate: false },
            })}>{base?.status === 'draft' ? 'Edit draft' : 'New version'}</Button>
          </div>
        }>
          <p className="mb-2 text-xs text-muted">Only one version is active. Issued documents keep the version (and OCR zones) they were printed with.</p>
          {templates.isPending ? <Empty>Loading…</Empty> : list.length === 0 ? <Empty>No templates.</Empty> : (
            <Table head={['Version', 'Status', 'Created', 'Checks', '']}>
              {list.map((t) => (
                <tr key={t.id}>
                  <Td>v{t.version}</Td><Td>{t.status}</Td><Td>{formatDate(t.created_at)}</Td>
                  <Td><Checks v={t.validation} /></Td>
                  <Td>{t.status === 'draft' && <Button variant="secondary" onClick={() => setEditing(t)}>Edit</Button>}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      )}
    </Page>
  )
}
