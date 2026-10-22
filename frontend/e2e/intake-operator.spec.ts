import { type APIRequestContext, type Page, expect, test } from '@playwright/test'
import { PNG, loginAPI, registerStation } from './support'

/*
 * Incoming documents (intake) station operator console. The station is simulated through its
 * API (X-Station-Key, docs/intake/screening-contract.md); the backend decides every tray.
 * Results are synthetic SAMPLEs: the field value "SAMPLE" must never reach the operator screen.
 */

/** A screening result that passes every rule (backend/tests/test_intake_decision.py clean()). */
function cleanResult() {
  return {
    schema: 1,
    agent_version: '0.3.0',
    capture: { source: 'file', width: 4608, height: 2592, attempts: 1 },
    quality: { ok: true, issues: [], blur_score: 412.3, brightness: 0.71, glare_fraction: 0.002, skew_deg: 0.8, coverage: 0.93, effective_dpi: 312 },
    doc_type: { predicted: 'psa_birth_certificate', confidence: 0.91, scores: { psa_birth_certificate: 0.91, jhs_report_card: 0.05 } },
    checks: {
      expected_headings_found: 4, expected_headings_total: 4, expected_fields_found: 9, expected_fields_total: 10,
      layout_score: 0.88, seal_logo_score: 0.8, font_consistency: 0.93, alignment_score: 0.9,
    },
    indicators: [] as object[],
    similarity: { score: 0.84, reference_count: 12, method: 'orb+layout' },
    fields: { last_name: { value: 'SAMPLE', confidence: 0.93 } },
    timings_ms: { capture: 900, quality: 40, screening: 2100 },
  }
}

/** Low type confidence + a severe indicator: needs manual review (Tray 2). */
function flaggedResult() {
  const result = cleanResult()
  result.doc_type = { ...result.doc_type, confidence: 0.6 }
  result.indicators = [{ code: 'altered_region', severity: 0.8, detail: 'SAMPLE', box: [1, 2, 3, 4] }]
  return result
}

/** The intake station, driven through its API. */
function intakeStation(request: APIRequestContext, key: string) {
  const headers = { 'X-Station-Key': key }
  return {
    async status(state: string, detail?: string) {
      const res = await request.post('/api/station/status', { headers, data: { state, detail } })
      expect(res.status(), await res.text()).toBe(200)
    },
    async startBatch() {
      const res = await request.post('/api/station/intake/batches', { headers })
      expect(res.status(), await res.text()).toBe(201)
      return ((await res.json()) as { batch_id: number }).batch_id
    },
    async submit(batchId: number, sequence: number, result: object) {
      const res = await request.post('/api/station/intake/documents', {
        headers,
        multipart: {
          result: JSON.stringify(result), batch_id: String(batchId), sequence: String(sequence),
          image: { name: 'page.png', mimeType: 'image/png', buffer: PNG },
        },
      })
      return res
    },
    async routed(intakeId: number, tray: 1 | 2) {
      const res = await request.post(`/api/station/intake/documents/${intakeId}/routed`, { headers, data: { tray } })
      expect(res.status(), await res.text()).toBe(200)
    },
  }
}

/** Station status + a batch with one page per tray, each routed where the backend said. */
async function batchOfTwo(request: APIRequestContext, key: string) {
  const station = intakeStation(request, key)
  await station.status('scanning')
  const batchId = await station.startBatch()
  const decisions: { decision: string; tray: number }[] = []
  for (const [sequence, result] of [[1, cleanResult()], [2, flaggedResult()]] as const) {
    const res = await station.submit(batchId, sequence, result)
    expect(res.status(), await res.text()).toBe(201)
    const body = (await res.json()) as { intake_id: number; decision: string; tray: 1 | 2 }
    decisions.push({ decision: body.decision, tray: body.tray })
    await station.routed(body.intake_id, body.tray)
  }
  expect(decisions).toEqual([{ decision: 'passed_screening', tray: 1 }, { decision: 'manual_review', tray: 2 }])
  return { station, batchId }
}

/** The validation operator opens the intake console and picks the test's own station. */
async function openConsole(page: Page, stationName: string) {
  await loginAPI(page, 'registrar')
  await page.goto('/')
  await page.getByRole('navigation').getByRole('link', { name: 'Intake station' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Incoming documents station' })).toBeVisible()
  await page.getByTestId('intake-station-select').selectOption({ label: stationName })
}

test('operator sees both trays, counts and plain-language reasons, never field values', async ({ page, browser, baseURL }) => {
  const registered = await registerStation(browser, baseURL!)
  const { station, batchId } = await batchOfTwo(page.request, registered.api_key)
  await openConsole(page, registered.name)

  await expect(page.getByTestId('intake-station-status')).toHaveAttribute('data-state', 'scanning')
  await expect(page.getByTestId('intake-station-status')).toContainText('Online · screening pages')

  // The latest page (#2) is the big indicator: Tray 2, needs manual review, with its reasons.
  const latest = page.getByTestId('intake-latest')
  await expect(latest).toHaveAttribute('data-tray', '2')
  await expect(page.getByTestId('intake-latest-tray')).toHaveText(/Tray 2$/)
  await expect(latest).toContainText('Needs manual review')
  await expect(latest).toContainText('Page #2')
  await expect(latest).toContainText('PSA birth certificate')
  await expect(latest).toContainText('The station was not confident about the document type.')

  await expect(page.getByTestId('intake-batch-heading')).toContainText(`Batch ${batchId} · running since`)
  await expect(page.getByTestId('intake-count-tray1')).toContainText(/Tray 1 · Passed screening\s*1$/)
  await expect(page.getByTestId('intake-count-tray2')).toContainText(/Tray 2 · Needs manual review\s*1$/)
  await expect(page.getByTestId('intake-count-total')).toHaveText('2 pages')

  const rows = page.getByTestId('intake-doc-row')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toHaveAttribute('data-tray', '2')
  await expect(rows.nth(0)).toContainText('#2')
  await expect(rows.nth(1)).toHaveAttribute('data-tray', '1')
  await expect(rows.nth(1)).toContainText('Tray 1 · Passed screening')

  // Operators see type, decision, tray and reasons only: no extracted values, no images.
  await expect(page.getByText('SAMPLE')).toHaveCount(0)
  await expect(page.locator('main img')).toHaveCount(0)
  await expect(page.getByText(/authentic|genuine|verified/i)).toHaveCount(0)

  // Ending the batch from the console; the station must start a new batch for more pages.
  await page.getByTestId('intake-end-batch').click()
  await page.getByTestId('intake-end-batch-confirm').click()
  await expect(page.getByTestId('intake-batch-heading')).toContainText(`last batch ${batchId} ended`)
  await expect(page.getByTestId('intake-end-batch')).toHaveCount(0)
  expect((await station.submit(batchId, 3, cleanResult())).status()).toBe(409)
})

test('operator sees an offline station with the agent message', async ({ page, browser, baseURL }) => {
  const registered = await registerStation(browser, baseURL!)
  const station = intakeStation(page.request, registered.api_key)
  await station.startBatch()
  await station.status('offline', 'Camera not found')
  await openConsole(page, registered.name)

  const banner = page.getByTestId('intake-station-status')
  await expect(banner).toHaveAttribute('data-state', 'offline')
  await expect(banner).toHaveText('✕ Station offline · Camera not found')
  await expect(page.getByTestId('intake-latest')).toContainText('Waiting for the first page of this batch')
})

test('intake console fits the TFT and shows the tray indicator', { tag: '@tft' }, async ({ page, browser, baseURL }) => {
  const registered = await registerStation(browser, baseURL!)
  await batchOfTwo(page.request, registered.api_key)
  await openConsole(page, registered.name)

  const viewport = page.viewportSize()!
  const latest = page.getByTestId('intake-latest')
  await expect(latest).toHaveAttribute('data-tray', '2')
  await latest.scrollIntoViewIfNeeded()
  await expect(latest).toBeInViewport()
  for (const id of ['intake-latest', 'intake-count-tray1', 'intake-count-tray2', 'intake-station-status']) {
    const box = (await page.getByTestId(id).boundingBox())!
    expect(box.x, id).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width, id).toBeLessThanOrEqual(viewport.width)
  }
  await expect(page.getByTestId('intake-count-tray1')).toContainText('1')
  await expect(page.getByTestId('intake-count-tray2')).toContainText('1')

  // No horizontal scrolling of the page at the panel's width.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `page is ${overflow}px wider than the ${viewport.width}px viewport`).toBeLessThanOrEqual(0)
})
