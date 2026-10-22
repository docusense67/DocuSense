export function formatDate(value: string | null | undefined, withTime = false): string {
  if (!value) return '—'
  const d = new Date(value.endsWith('Z') || value.includes('+') ? value : value + 'Z')
  return withTime ? d.toLocaleString() : d.toLocaleDateString()
}
