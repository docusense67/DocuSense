import { type Page, expect, test } from '@playwright/test'
import { SAMPLE_NUMBER, currentSessionId, loginAPI, registerStation, scans, submitScan } from './support'

/** Registrar (validation operator) opens the scan station page and starts a session. */
async function startSession(page: Page, stationName: string, mode: 'Single document' | 'Bulk stack') {
  await loginAPI(page, 'registrar')
  await page.goto('/')
  await page.getByRole('navigation').getByRole('link', { name: 'Scan station' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Scan station' })).toBeVisible()
  await expect(page.getByText('Choose a station first.')).toBeVisible()
  await expect(page.getByRole('button', { name: mode })).toBeDisabled()

  await page.getByLabel('Station').selectOption({ label: stationName })
  await page.getByRole('button', { name: mode }).click()
  const tray = mode === 'Single document' ? 'SINGLE' : 'BULK'
  await expect(page.getByText(/place paper in the feeder; collect it from the/))
    .toHaveText(new RegExp(`^Session \\d+ \\((single|bulk)\\) · place paper in the feeder; collect it from the ${tray} tray\\.$`))
  return currentSessionId(page)
}

const sessionCard = (page: Page) =>
  page.locator('section', { has: page.getByRole('heading', { name: /^Session \d+ · (single|bulk)/ }) })

test('operator starts a Single session and sees VALID / INVALID / NEEDS REVIEW results', async ({ page, browser, baseURL }) => {
  const station = await registerStation(browser, baseURL!)
  const sessionId = await startSession(page, station.name, 'Single document')
  const card = sessionCard(page)
  await expect(card.getByText('Waiting for the station to send results…')).toBeVisible()
  await expect(page.getByText('The latest result will appear here.')).toBeVisible()

  // The station (simulated through its API) sends three scans; decisions are the backend's.
  const results: string[] = []
  for (const payload of [scans.valid(1), scans.invalid(2), scans.needsReview(3)]) {
    results.push((await submitScan(page.request, station.api_key, sessionId, payload)).result)
  }
  expect(results).toEqual(['VALID', 'INVALID', 'NEEDS_REVIEW'])

  // The dashboard polls the session and lists every result with its badge.
  const rows = card.getByRole('row')
  await expect(rows).toHaveCount(4) // header + 3
  await expect(rows.nth(1)).toContainText(SAMPLE_NUMBER)
  await expect(rows.nth(1)).toContainText(/VALID/)
  await expect(rows.nth(2)).toContainText('INVALID')
  await expect(rows.nth(3)).toContainText('NEEDS REVIEW')
  await expect(card.getByText('Total').locator('..')).toContainText('3')

  // The big result panel follows the latest scan (needs review -> registrar decision).
  await expect(page.getByText('! Registrar decision needed')).toBeVisible()
  // Pin the VALID one: Section 15 wording, "matches official records", never "authentic".
  await rows.nth(1).getByRole('button', { name: SAMPLE_NUMBER }).click()
  await expect(page.getByText(/Information matches official records as of/)).toBeVisible()
  await expect(page.getByText(/authentic/i)).toHaveCount(0)

  await page.getByRole('button', { name: 'End session' }).click()
  await expect(card.getByRole('heading', { name: /\(ended\)/ })).toBeVisible()
})

test('operator dashboard fits the TFT and shows the result', { tag: '@tft' }, async ({ page, browser, baseURL }) => {
  const station = await registerStation(browser, baseURL!)
  const sessionId = await startSession(page, station.name, 'Single document')
  await submitScan(page.request, station.api_key, sessionId, scans.valid(1))

  const viewport = page.viewportSize()!
  // The large result badge is the status area the operator reads at the station.
  const result = page.locator('span.text-3xl', { hasText: 'VALID' })
  await expect(result).toBeVisible()
  await result.scrollIntoViewIfNeeded()
  await expect(result).toBeInViewport()
  const box = (await result.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(0)
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width)
  await expect(page.getByText(/collect it from the SINGLE tray/)).toBeVisible()

  // No horizontal scrolling of the page at the panel's width.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow, `page is ${overflow}px wider than the ${viewport.width}px viewport`).toBeLessThanOrEqual(0)
})
