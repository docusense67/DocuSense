/** Shared helpers for the end-to-end tests. Demo data comes from backend/scripts/dev_server.py. */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { type APIRequestContext, type Browser, type Page, expect } from '@playwright/test'

export const PASSWORD = 'ChangeMe-2026' // demo accounts only (app/seed.py); forced change is off in dev_server
export const email = (handle: string) => `${handle}@school.edu.ph`

/** A short unique suffix so parallel tests never look at each other's data. */
export const uniq = () => `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

/** Sign in through the login form. */
export async function loginUI(page: Page, handle: string) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Email').fill(email(handle))
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
}

/** Sign in through the API; the session cookie lands in the page's browser context. */
export async function loginAPI(page: Page, handle: string) {
  await signIn(page.request, handle)
}

async function signIn(request: APIRequestContext, handle: string) {
  const res = await request.post('/api/auth/login', { data: { email: email(handle), password: PASSWORD } })
  expect(res.status(), `login as ${handle}`).toBe(200)
}

/** A second, independent browser session (e.g. the registrar next to the student). */
export async function pageAs(browser: Browser, handle: string, baseURL: string) {
  const context = await browser.newContext({ baseURL })
  const page = await context.newPage()
  await loginAPI(page, handle)
  return page
}

type DocRequest = { id: number; reference_no: string; student_number: string; document_type: string; status: string }
// Open requests that can still be cancelled (S-11 order). 'verified' only exists when release is
// manual, which dev_server.py never configures.
const OPEN = ['requested', 'approved', 'for_verification']

/**
 * The backend allows one open request per student and type. Cancel leftovers (from an aborted
 * earlier run against a reused dev server) so the test can file its own request.
 */
export async function cancelOpenRequests(page: Page, studentNumber: string, type: 'TOR' | 'COE') {
  const res = await page.request.get('/api/requests')
  expect(res.ok()).toBeTruthy()
  for (const r of (await res.json()) as DocRequest[]) {
    if (r.student_number === studentNumber && r.document_type === type && OPEN.includes(r.status)) {
      const cancel = await page.request.post(`/api/requests/${r.id}/cancel`, { data: { reason: 'E2E cleanup of a leftover request' } })
      expect(cancel.ok(), `cancel ${r.reference_no}`).toBeTruthy()
    }
  }
}

// ---- scan station simulation (station API, X-Station-Key) -----------------------------------

/** Register a fresh station as admin, so each test has its own sessions. */
export async function registerStation(browser: Browser, baseURL: string) {
  const admin = await pageAs(browser, 'admin', baseURL)
  const res = await admin.request.post('/api/stations', { data: { name: `E2E Station ${uniq()}` } })
  expect(res.status()).toBe(201)
  const station = (await res.json()) as { id: number; name: string; api_key: string }
  await admin.context().close()
  return station
}

type Snapshot = {
  document_number: string
  terms: { academic_year: string; semester: string; subjects: Record<string, string>[] }[]
  [field: string]: unknown
}
type Fixture = { data_hash: string; qr_token: string; snapshot: Snapshot }

/** Maria's TOR that dev_server.py pre-issues (the CI sample, scan-agent/tests/fixtures). */
const SAMPLE: Fixture = JSON.parse(readFileSync(
  resolve(import.meta.dirname, '../../scan-agent/tests/fixtures/TOR-2026-000001.json'), 'utf-8'))
export const SAMPLE_NUMBER = SAMPLE.snapshot.document_number

/** What the scan agent would extract from a perfect scan of the pre-issued sample TOR. */
export function sampleExtraction(position: number) {
  const snap = SAMPLE.snapshot
  const fields = Object.fromEntries(
    ['document_number', 'student_name', 'student_number', 'program', 'date_issued', 'gwa']
      .map((k) => [k, { value: snap[k], confidence: 96 }]))
  const rows = snap.terms.flatMap((t) => t.subjects.map((s) => ({
    term: `${t.semester}, AY ${t.academic_year}`, ...s,
    confidence: { subject_code: 95, units: 95, grade: 95 },
  })))
  return {
    position,
    identity_source: 'qr' as string,
    qr_token: SAMPLE.qr_token as string | null,
    document_number_read: snap.document_number,
    pages: [{ sequence: 1, qr_present: true,
      footer: { document_number: snap.document_number, page: 1, pages: 1, ref: SAMPLE.data_hash.slice(0, 12) } }],
    fields,
    table: { rows, term_gwas: [], unparsed: [] },
  }
}

/** Expected results per the backend rules (backend/tests/test_scanning.py). */
export const scans = {
  valid: (position: number) => sampleExtraction(position),
  // Number never issued by the school, read by OCR: INVALID.
  invalid: (position: number) => ({ ...sampleExtraction(position), qr_token: null, identity_source: 'ocr',
    document_number_read: 'TOR-2026-424242' }),
  // Real number but no QR read: NEEDS_REVIEW.
  needsReview: (position: number) => ({ ...sampleExtraction(position), qr_token: null, identity_source: 'ocr' }),
}

// A real (blank, 210x297 grayscale) PNG: the API decodes and recompresses every page, so
// placeholder bytes get a 422.
export const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAANIAAAEpCAAAAADD0CPwAAABD0lEQVR42u3PAREAAAQEsKd/ZwJo4LYGq8k3HSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSWlawHodANRFFRy+gAAAABJRU5ErkJggg==',
  'base64',
)

/** Submit one scanned document as the station would. Returns the backend's result. */
export async function submitScan(request: APIRequestContext, key: string, sessionId: number, payload: object) {
  const res = await request.post(`/api/station/sessions/${sessionId}/documents`, {
    headers: { 'X-Station-Key': key },
    multipart: { payload: JSON.stringify(payload), pages: { name: 'p1.png', mimeType: 'image/png', buffer: PNG } },
  })
  expect(res.status(), await res.text()).toBe(201)
  return (await res.json()) as { scanned_document_id: number; result: string }
}

/** The operator dashboard's current session id (OperatorHome keeps it in localStorage). */
export async function currentSessionId(page: Page) {
  const id = await page.evaluate(() => Number(localStorage.getItem('adgvs.session')))
  expect(id).toBeGreaterThan(0)
  return id
}
