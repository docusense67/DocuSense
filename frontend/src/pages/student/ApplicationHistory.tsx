import { useQuery } from '@tanstack/react-query'
import { api, type ApplicationHistoryItem } from '../../api'
import { Card, Empty, ErrorText, Page, Table, Td } from '../../components/ui'
import { formatDate } from '../../lib/format'

// docs/applicant-intake.md: a student who came in as an applicant keeps a read-only list of the
// documents they sent, with one plain status each (no staff notes, reasons or readings).

const TYPE_LABEL: Record<ApplicationHistoryItem['declared_type'], string> = {
  transcript_of_records: 'Transcript of Records',
  certificate_of_enrollment: 'Certificate of Enrollment',
}

export function ApplicationHistory() {
  const history = useQuery({ queryKey: ['my-applications'], queryFn: api.myApplications })
  const rows = history.data ?? []
  return (
    <Page title="Application history">
      <Card>
        <ErrorText error={history.error} />
        {history.isPending ? <Empty>Loading…</Empty> : rows.length === 0
          ? <Empty>No documents were sent from this login as an applicant.</Empty>
          : (
            <Table head={['Document', 'School', 'Sent', 'Status']}>
              {rows.map((a) => (
                <tr key={a.id} data-testid="application-history-row">
                  <Td>{TYPE_LABEL[a.declared_type]}</Td>
                  <Td>{a.issuing_school}</Td>
                  <Td>{formatDate(a.submitted_at)}</Td>
                  <Td>{a.status_text}</Td>
                </tr>
              ))}
            </Table>
          )}
      </Card>
    </Page>
  )
}
