import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api'
import { Empty, Page } from '../../components/ui'
import { ScanResultView } from './ScanResultView'

export function ScanDetailPage() {
  const id = Number(useParams().id)
  const detail = useQuery({ queryKey: ['scan', id], queryFn: () => api.scanDetail(id) })
  return (
    <Page title="Scan result" actions={<Link to="/scan" className="text-sm text-accent hover:underline">← Scan station</Link>}>
      {detail.data ? <ScanResultView detail={detail.data} /> : <Empty>{detail.error ? (detail.error as Error).message : 'Loading…'}</Empty>}
    </Page>
  )
}
