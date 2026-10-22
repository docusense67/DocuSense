import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Fragment, useState } from 'react'
import { api, type FieldResult, type ScanDetail, type Zone } from '../../api'
import { Button, Card, ErrorText, Field, ResultBadge, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { ScannedPaperText } from './ScannedPaperText'

const PAGE_W_MM = 210

/** Shows one zone of a scanned A4 page (zones are mm from the page's top-left). */
function Crop({ src, zone, width = 220 }: { src: string; zone: Zone; width?: number }) {
  const pad = 1
  const z = { x: zone.x - pad, y: zone.y - pad, w: zone.w + 2 * pad, h: zone.h + 2 * pad }
  const scale = width / z.w // px per mm
  return (
    <div className="relative max-w-full overflow-hidden rounded border border-line bg-white" style={{ width, height: Math.max(18, z.h * scale) }}>
      <img src={src} alt="" className="absolute max-w-none"
        style={{ width: PAGE_W_MM * scale, left: -z.x * scale, top: -z.y * scale }} />
    </div>
  )
}

const STATUS: Record<FieldResult['status'], { icon: string; label: string; cls: string }> = {
  match: { icon: '✓', label: 'Match', cls: 'text-good-ink' },
  mismatch: { icon: '✕', label: 'Mismatch', cls: 'text-critical font-semibold' },
  similar: { icon: '!', label: 'Similar', cls: 'text-ink font-semibold' },
  uncertain: { icon: '!', label: 'Low confidence', cls: 'text-ink font-semibold' },
  missing: { icon: '!', label: 'Not read', cls: 'text-ink font-semibold' },
}

function zoneFor(f: FieldResult, zones: ScanDetail['zones']): (Zone & { page?: number }) | null {
  if (f.zone) return f.zone // exact row position reported by the scan agent
  if (!zones) return null
  const field = f.field
  const direct = zones.fields[field]
  if (direct) return direct
  if (/^(grade|units|term GWA)/.test(field)) return zones.fields['grades_table'] ?? null
  return null
}

function Review({ detail }: { detail: ScanDetail }) {
  const qc = useQueryClient()
  const [remarks, setRemarks] = useState('')
  const review = useMutation({
    mutationFn: (final: 'VALID' | 'INVALID') => api.review(detail.scanned_document_id, final, remarks),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scan', detail.scanned_document_id] })
      qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
  if (detail.result !== 'NEEDS_REVIEW') return null
  if (detail.review.final_result) {
    return (
      <p className="text-sm text-ink-2">Reviewed: confirmed <ResultBadge result={detail.review.final_result} />, {detail.review.remarks}
        <span className="block text-xs text-muted">The automatic result (NEEDS REVIEW) is kept on record.</span></p>
    )
  }
  return (
    <div className="space-y-2 rounded-md border border-warning p-3">
      <p className="text-sm font-semibold text-ink">! Registrar decision needed</p>
      <Field label="Remarks (what you checked)" value={remarks} onChange={(e) => setRemarks(e.target.value)} />
      <div className="flex gap-2">
        <Button disabled={remarks.trim().length < 3} onClick={() => review.mutate('VALID')} busy={review.isPending}>Confirm valid</Button>
        <Button variant="danger" disabled={remarks.trim().length < 3} onClick={() => review.mutate('INVALID')}>Confirm invalid</Button>
      </div>
      <ErrorText error={review.error} />
    </div>
  )
}

export function ScanResultView({ detail }: { detail: ScanDetail }) {
  const pageUrl = (n: number) => `/api/validations/${detail.scanned_document_id}/pages/${n}`
  const page1 = detail.page_images > 0 ? pageUrl(1) : null
  const ordered = [...detail.fields].sort((a, b) => Number(a.status === 'match') - Number(b.status === 'match'))
  return (
    <Card>
      <div className="flex flex-wrap items-start gap-5">
        {detail.result && <ResultBadge result={detail.result} size="xl" />}
        <div className="min-w-0 flex-1 space-y-1 text-sm">
          {detail.document ? (
            <>
              <div className="text-base font-semibold text-ink">{detail.document.student_name}</div>
              <div className="num text-ink-2">{detail.document.document_type} · {detail.document.document_number}
                {' '}· scanned {detail.document.times_scanned} time(s) · document {detail.document.status}</div>
            </>
          ) : <div className="text-ink-2">Read as: <span className="num">{detail.document_number ?? 'unreadable'}</span></div>}
          <div className="text-xs text-muted">#{detail.position ?? '—'} in stack · {formatDate(detail.scanned_at, true)}</div>
          <ul className="mt-2 list-inside list-disc text-ink">
            {detail.reasons.map((r) => (
              // Section 15 wording: VALID is a statement about the records at a point in time.
              <li key={r}>{detail.result === 'VALID' && r === 'Information matches official records'
                ? `${r} as of ${formatDate(detail.scanned_at, true)}.` : r}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="mt-4"><Review detail={detail} /></div>

      <ScannedPaperText pages={detail.ocr_pages} />

      {detail.fields.length > 0 && (
        <div className="mt-4">
          <Table head={['Field', 'On paper', 'Official', 'Result']}>
            {ordered.map((f) => {
              const s = STATUS[f.status]
              const zone = zoneFor(f, detail.zones)
              const showCrop = page1 && zone && f.status !== 'match'
              return (
                <Fragment key={f.field}>
                  <tr className={f.status === 'match' ? '' : 'bg-surface-2'}>
                    <Td className="text-ink-2">{f.field.replaceAll('_', ' ')}{f.critical && <span className="ml-1 text-xs text-muted">(critical)</span>}</Td>
                    <Td className="num">{f.printed ?? '—'}</Td>
                    <Td className="num">{f.official ?? '—'}</Td>
                    <Td><span className={s.cls}><span aria-hidden>{s.icon}</span> {s.label}</span>
                      {f.confidence !== null && <span className="block text-xs text-muted">OCR {Math.round(f.confidence)}%</span>}</Td>
                  </tr>
                  {showCrop && (
                    // The scanned area itself, large enough to judge by eye (Section 15).
                    <tr className="bg-surface-2">
                      <td colSpan={4} className="px-2 pb-3">
                        <span className="mb-1 block text-xs text-muted">On the scan (page {zone.page ?? 1}):</span>
                        <Crop src={pageUrl(zone.page ?? 1)} zone={zone} width={600} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </Table>
        </div>
      )}

      {page1 && (
        <p className="mt-3 text-xs text-muted">
          Scanned pages: {Array.from({ length: detail.page_images }, (_, i) => (
            <a key={i} href={`/api/validations/${detail.scanned_document_id}/pages/${i + 1}`} target="_blank" rel="noopener" className="mr-2 text-accent hover:underline">page {i + 1}</a>
          ))}
        </p>
      )}
      <p className="mt-3 border-t border-line pt-2 text-xs text-muted">{detail.note}</p>
    </Card>
  )
}
