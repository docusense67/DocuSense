/**
 * Incoming enrollment documents, end to end (Part I, S-04/S-09 + S-06/S-07): the intake station
 * (simulated through its API) screens two pages, one passes and one needs manual review; the
 * registrar works the queue, accepts the flagged page with a reason, reviews the fields read from
 * it, creates the student profile and finds the document on the student's record.
 * All people here are synthetic SAMPLEs.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { type APIRequestContext, expect, test } from '@playwright/test'
import { loginAPI, registerStation, uniq } from './support'

// A real, decodable JPEG: the API decodes and recompresses every page (placeholder bytes get 422).
const PAGE = readFileSync(resolve(import.meta.dirname, 'fixtures/intake-page-sample.jpg'))

/** Names may only hold letters: turn the unique suffix's digits into letters. */
const letters = (s: string) => s.replace(/\d/g, (d) => 'abcdefghij'[Number(d)])

function screeningResult(fields: Record<string, { value: string; confidence: number }>, flagged: boolean) {
  return {
    schema: 1,
    agent_version: 'e2e',
    capture: { source: 'file', width: 2480, height: 3508, attempts: 1 },
    quality: { ok: true, issues: [], blur_score: 400, brightness: 0.7, glare_fraction: 0, skew_deg: 0.5, coverage: 0.95, effective_dpi: 300 },
    doc_type: { predicted: 'psa_birth_certificate', confidence: 0.93, scores: { psa_birth_certificate: 0.93 } },
    checks: {
      expected_headings_found: 4, expected_headings_total: 4, expected_fields_found: 9, expected_fields_total: 10,
      // Below the 0.70 default: the backend sends this page to manual review (Tray 2).
      layout_score: flagged ? 0.4 : 0.9,
      seal_logo_score: 0.8, font_consistency: 0.9, alignment_score: 0.9,
    },
    indicators: [],
    similarity: { score: 0.85, reference_count: 8, method: 'orb+layout' },
    fields,
    timings_ms: { capture: 800, quality: 30, screening: 1500 },
  }
}

async function submitIntake(request: APIRequestContext, key: string, result: object) {
  const res = await request.post('/api/station/intake/documents', {
    headers: { 'X-Station-Key': key },
    multipart: { result: JSON.stringify(result), image: { name: 'page.jpg', mimeType: 'image/jpeg', buffer: PAGE } },
  })
  expect(res.status(), await res.text()).toBe(201)
  return (await res.json()) as { intake_id: number; decision: string; tray: number; reasons: string[] }
}

test('intake: screen, review the queue, accept a flagged page, review fields, create the student', async ({ page, browser, baseURL }) => {
  test.setTimeout(90_000)
  const tag = letters(uniq())
  const last = `Sample${tag}`
  const read = (value: string, confidence = 0.95) => ({ value, confidence })
  const fields = {
    child_last_name: read(last.toUpperCase()),
    child_first_name: read('JUANITO'),
    child_middle_name: read('PEREZ', 0.55), // the station was unsure: highlighted for checking
    sex: read('M', 0.9),
    date_of_birth: read('MARCH 5, 2010', 0.85),
    place_of_birth: read('QUEZON CITY', 0.9),
    mother_first_name: read('MARIA'), mother_last_name: read('PEREZ'),
  }

  // ---- the station screens two pages ----
  const station = await registerStation(browser, baseURL!)
  const passed = await submitIntake(page.request, station.api_key, screeningResult(fields, false))
  const flagged = await submitIntake(page.request, station.api_key, screeningResult(fields, true))
  expect(passed).toMatchObject({ decision: 'passed_screening', tray: 1, reasons: [] })
  expect(flagged).toMatchObject({ decision: 'manual_review', tray: 2 })
  expect(flagged.reasons).toContain('layout_score_low')

  // ---- the registrar's review queue ----
  await loginAPI(page, 'registrar')
  await page.goto('/')
  await page.getByRole('navigation').getByRole('link', { name: 'Incoming documents' }).click()
  const queue = page.getByTestId('intake-queue')
  const passedRow = queue.locator(`[data-intake-id="${passed.intake_id}"]`)
  const flaggedRow = queue.locator(`[data-intake-id="${flagged.intake_id}"]`)
  await expect(passedRow.getByTestId('intake-decision')).toHaveAttribute('data-decision', 'passed_screening')
  await expect(flaggedRow.getByTestId('intake-decision')).toHaveAttribute('data-decision', 'manual_review')
  await expect(flaggedRow.getByTestId('intake-status')).toHaveAttribute('data-status', 'screened')

  // ---- accept the flagged page with a reason ----
  await flaggedRow.getByRole('link', { name: `#${flagged.intake_id}` }).click()
  await expect(page.getByTestId('intake-reason-item').first()).toBeVisible()
  await expect(page.getByTestId('intake-image')).toBeVisible()
  // Its data can't be used before a person accepts it.
  await expect(page.getByTestId('student-data-blocked')).toContainText('accept it')
  await expect(page.getByTestId('intake-accept')).toBeDisabled() // a reason is required
  await page.getByTestId('intake-reason').fill('Layout checked against the original at the window')
  await page.getByTestId('intake-accept').click()
  await expect(page.getByTestId('intake-reviewed')).toContainText('Accepted')

  // ---- review the fields read from the page ----
  const form = page.getByTestId('student-data-form')
  await expect(form).toBeVisible()
  const field = (key: string) => form.getByTestId(`field-${key}`)
  await expect(field('last_name')).toHaveValue(last.toUpperCase())
  await expect(field('birth_date')).toHaveValue('2010-03-05') // "MARCH 5, 2010" as printed
  await expect(field('sex')).toHaveValue('Male')
  await expect(field('mother_name')).toHaveValue('MARIA PEREZ')
  const middle = form.locator('[data-key="middle_name"]')
  await expect(middle.getByTestId('field-confidence')).toHaveAttribute('data-low', 'true')
  await expect(middle.getByTestId('field-confidence')).toContainText('55% confident')

  // Correct the names; a future birth date is refused by the backend, field by field.
  await field('last_name').fill(last)
  await field('first_name').fill('Juanito')
  await field('middle_name').fill('Pereza')
  await field('birth_date').fill('2099-01-01')
  await page.getByTestId('student-data-save').click()
  await expect(form.locator('[data-key="birth_date"]').getByTestId('field-error')).toHaveText('Date is in the future')
  await field('birth_date').fill('2010-03-05')
  await field('first_name').fill('')
  await expect(page.getByTestId('student-data-save')).toBeDisabled() // required name missing
  await field('first_name').fill('Juanito')
  await page.getByTestId('student-data-save').click()

  // ---- no existing student: create the profile and link ----
  await expect(page.getByTestId('student-data')).toHaveAttribute('data-status', 'data_reviewed')
  await expect(page.getByTestId('no-candidates')).toBeVisible()
  const studentNumber = `E2E-${uniq()}`.slice(0, 30)
  await page.getByTestId('create-student-number').fill(studentNumber)
  await page.getByTestId('create-student').click()
  const linked = page.getByTestId('student-linked')
  await expect(linked).toContainText(`${last}, Juanito Pereza (${studentNumber})`)
  await expect(page.getByTestId('student-data')).toHaveAttribute('data-status', 'linked')

  // The audit trail names the steps, never the values.
  const history = page.getByTestId('intake-history')
  await expect(history).toContainText('intake fields reviewed')
  await expect(history).toContainText('intake document linked')
  await expect(history).not.toContainText(last)

  // ---- the student's record lists the document with its page ----
  await linked.getByTestId('student-linked-record').click()
  await expect(page.getByRole('heading', { level: 1, name: 'Student record' })).toBeVisible()
  const profile = page.getByTestId('student-profile')
  await expect(profile).toContainText(studentNumber)
  await expect(profile.locator('[data-field="Date of birth"]')).toHaveText('2010-03-05')
  await expect(profile.locator('[data-field="Place of birth"]')).toHaveText('QUEZON CITY')
  await expect(profile.locator('[data-field="Mother\'s maiden name"]')).toHaveText('MARIA PEREZ')
  const docRow = page.getByTestId('student-intake-doc')
  await expect(docRow).toHaveCount(1)
  await expect(docRow).toHaveAttribute('data-intake-id', String(flagged.intake_id))
  await expect(docRow.getByTestId('intake-status')).toHaveAttribute('data-status', 'linked')
  await docRow.getByTestId('student-intake-thumb').click()
  const image = page.getByTestId('student-intake-image')
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth)).toBeGreaterThan(0)

  // The record is reachable from the master list too.
  await page.goto('/records')
  await page.getByRole('textbox', { name: 'Search students' }).fill(studentNumber)
  await page.getByRole('row', { name: new RegExp(studentNumber) }).getByTestId('student-record-link').click()
  await expect(page.getByTestId('student-intake-doc')).toHaveCount(1)

  // The page that passed screening still waits in the queue.
  await page.goto('/intake')
  await expect(page.getByTestId('intake-queue').locator(`[data-intake-id="${passed.intake_id}"]`)).toBeVisible()
})
