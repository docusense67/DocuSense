import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, type ScanDetail } from '../../api'

export function ScannedPaperText({ pages, message }: {
  pages?: ScanDetail['ocr_pages']; message?: string
}) {
  return <section aria-label="Text read from scanned paper" className="my-4 rounded-md border border-line p-3">
    <h3 className="text-sm font-semibold text-ink">Text read from scanned paper (OCR)</h3>
    <p className="mt-1 text-xs text-muted">Text extracted from the scanned image. OCR can misread characters; compare with the paper.</p>
    {message ? <p role="status" className="mt-3 text-sm text-ink-2">{message}</p>
      : (pages?.length ?? 0) > 0 ? pages!.map((page) => (
        <div key={page.page} className="mt-3">
          <p className="mb-1 text-xs font-medium text-ink-2">Page {page.page}</p>
          {page.text !== null && page.text.trim()
            ? <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-2 p-3 font-mono text-sm text-ink">{page.text}</pre>
            : <p className="text-sm text-muted">{page.error || (page.text === null
              ? 'Full-page text was not saved for this scan. Scan again to capture it.'
              : 'No readable text was found. Check the lighting, focus and paper position.')}</p>}
          {page.truncated && <p className="mt-1 text-xs text-muted">Showing the first 50,000 characters of this page.</p>}
        </div>
      )) : <p className="mt-3 text-sm text-muted">Full-page text was not saved for this older scan. Scan again to capture it; extracted field comparisons remain in the scan result.</p>}
  </section>
}

export function CurrentScanText({ scanId, waiting, interrupted }: {
  scanId: number | null; waiting: boolean; interrupted: boolean
}) {
  const detail = useQuery({
    queryKey: ['scan', scanId], queryFn: () => api.scanDetail(scanId!), enabled: !!scanId,
  })
  let message: string | undefined
  if (waiting) message = 'Scanning paper and reading text… The OCR text will appear here automatically.'
  else if (!scanId) message = interrupted
    ? 'The scan stopped before text was saved. Clear the paper path before scanning again.'
    : 'Scan one sheet to see its OCR text here.'
  else if (detail.isPending) message = 'Loading text from the scanned paper…'
  else if (detail.error) message = `Could not load OCR text: ${detail.error.message}`
  return <div>
    <ScannedPaperText pages={detail.data?.ocr_pages} message={message} />
    {scanId && !waiting && <p className="-mt-2 mb-4 text-xs text-muted">Scanned paper #{scanId} · <Link className="text-accent underline" to={`/scan/${scanId}`}>Open saved result and scanned image</Link></p>}
  </div>
}
