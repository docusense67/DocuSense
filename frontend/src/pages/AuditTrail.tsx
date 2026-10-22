import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { Button, Card, Empty, Field, Page, Table, Td } from '../components/ui'
import { describe } from '../lib/audit'
import { formatDate } from '../lib/format'

const PAGE = 50

/** Section 5H / 17: search the append-only audit trail. Read-only for everyone. */
export function AuditTrail() {
  const [draft, setDraft] = useState({ q: '', date_from: '', date_to: '' })
  const [filters, setFilters] = useState(draft)
  const [offset, setOffset] = useState(0)
  const logs = useQuery({
    queryKey: ['audit', filters, offset],
    queryFn: () => api.auditLogs({ ...filters, offset, limit: PAGE }),
    placeholderData: keepPreviousData,
  })
  const total = logs.data?.total ?? 0
  const apply = () => { setFilters(draft); setOffset(0) }

  return (
    <Page title="Audit trail">
      <Card>
        <form onSubmit={(e) => { e.preventDefault(); apply() }} className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-end">
          <Field label="Search" placeholder="Document number, student number, email, action…" value={draft.q}
            onChange={(e) => setDraft({ ...draft, q: e.target.value })} />
          <Field label="From" type="date" value={draft.date_from} onChange={(e) => setDraft({ ...draft, date_from: e.target.value })} />
          <Field label="To" type="date" value={draft.date_to} onChange={(e) => setDraft({ ...draft, date_to: e.target.value })} />
          <Button type="submit">Search</Button>
        </form>
        <p className="mt-2 text-xs text-muted">Entries cannot be edited or deleted by anyone, including administrators.</p>
      </Card>
      <Card title={logs.data ? `${total} entr${total === 1 ? 'y' : 'ies'}` : 'Entries'}>
        {logs.isPending ? <Empty>Loading…</Empty> : total === 0 ? <Empty>No matching entries.</Empty> : (
          <>
            <Table head={['When', 'Who', 'Action', 'Record', 'Details']}>
              {logs.data!.items.map((e) => (
                <tr key={e.id}>
                  <Td className="whitespace-nowrap text-ink-2">{formatDate(e.timestamp, true)}</Td>
                  <Td>{e.user_email ?? 'system'}{e.ip_address && <span className="block text-xs text-muted">{e.ip_address}</span>}</Td>
                  <Td className="whitespace-nowrap">{e.action.replaceAll('_', ' ')}</Td>
                  <Td className="whitespace-nowrap text-ink-2">{e.entity_type ? `${e.entity_type.replaceAll('_', ' ')} #${e.entity_id}` : '—'}</Td>
                  <Td className="text-xs text-ink-2">
                    {e.old_value && <span className="block">before: {describe(e.old_value)}</span>}
                    {describe(e.new_value)}
                  </Td>
                </tr>
              ))}
            </Table>
            <div className="mt-3 flex items-center justify-between text-sm text-ink-2">
              <span>{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
              <span className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>Newer</Button>
                <Button variant="secondary" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Older</Button>
              </span>
            </div>
          </>
        )}
      </Card>
    </Page>
  )
}
