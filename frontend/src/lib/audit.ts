// Readable one-line summary of an audit entry's values ("document number: TOR-..., result: VALID").
const SKIP = new Set(['scanned_document_id', 'request_id', 'session_id'])

export function describe(values: Record<string, unknown> | null | undefined, max = 180): string {
  if (!values) return ''
  const parts: string[] = []
  for (const [key, value] of Object.entries(values)) {
    if (SKIP.has(key) || value === null || value === undefined || value === '') continue
    if (Array.isArray(value) && value.length === 0) continue
    const text = typeof value === 'object' ? JSON.stringify(value) : String(value)
    parts.push(`${key.replaceAll('_', ' ')}: ${text}`)
  }
  const line = parts.join(' · ')
  return line.length > max ? line.slice(0, max - 1) + '…' : line
}
