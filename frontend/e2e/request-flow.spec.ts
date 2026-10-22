import { type Page, expect, test } from '@playwright/test'
import { cancelOpenRequests, loginAPI, pageAs, uniq } from './support'

/** The student's home page, once the eligibility check behind the request form has loaded. */
async function openHome(page: Page) {
  const eligibility = page.waitForResponse((r) => r.url().endsWith('/api/requests/eligibility') && r.ok())
  await page.goto('/')
  await eligibility
  await expect(page.getByRole('heading', { level: 1, name: 'My documents' })).toBeVisible()
}

/** File a request through the student's form and return its reference number. */
async function fileRequest(page: Page, label: RegExp, purpose: string) {
  await openHome(page)
  const form = page.locator('section', { has: page.getByRole('heading', { name: 'Request a document' }) })
  const choice = form.getByRole('option', { name: label })
  await expect(choice).not.toHaveAttribute('disabled') // eligible
  await form.getByLabel('Document').selectOption({ label: (await choice.textContent())! })
  await form.getByLabel('Purpose').fill(purpose)
  await form.getByRole('button', { name: 'Submit' }).click()
  const done = form.getByText(/Request REQ-\d{4}-\d{6} submitted/)
  await expect(done).toBeVisible()
  return (await done.textContent())!.match(/REQ-\d{4}-\d{6}/)![0]
}

/** The student's own row for this request in "My requests". */
const myRow = (page: Page, ref: string) =>
  page.locator('section', { has: page.getByRole('heading', { name: 'My requests' }) })
    .getByRole('row').filter({ hasText: ref })

/** Click "Download PDF" and check the protected link it asks for actually serves the PDF. */
async function downloadWorks(page: Page, ref: string) {
  const row = myRow(page, ref)
  await expect(row.getByText(/Download until/)).toBeVisible()
  const linkResponse = page.waitForResponse((r) => /\/api\/documents\/\d+\/download-link$/.test(r.url()))
  const popup = page.waitForEvent('popup').catch(() => null)
  await row.getByRole('button', { name: 'Download PDF' }).click()
  const res = await linkResponse
  expect(res.status()).toBe(200)
  const { url } = (await res.json()) as { url: string }
  expect(url).toMatch(/^\/api\//)
  await (await popup)?.close()

  // Assert the response, not the PDF rendering.
  const pdf = await page.request.get(url)
  expect(pdf.status()).toBe(200)
  expect(pdf.headers()['content-type']).toContain('application/pdf')
  expect((await pdf.body()).subarray(0, 5).toString()).toBe('%PDF-')
}

/** Open a request from a dashboard queue (Approval queue / Verification queue). */
const queueCard = (page: Page, title: string) =>
  page.locator('section', { has: page.getByRole('heading', { name: title }) })

/** The registrar's review screen shows a status badge with exactly this label. */
const badge = (page: Page, label: string) => page.getByText(label, { exact: true })

// Approve-first order (change S-11): the registrar approves the REQUEST, the system then retrieves
// the records and generates the document, the registrar verifies it, and release completes it.
// Students see Pending -> Approved -> Completed; the registrar sees the sub-steps.

test('student requests a TOR, registrar approves it, verifies the document, student downloads it', async ({ page, browser, baseURL }) => {
  await loginAPI(page, 'maria.santos')
  await cancelOpenRequests(page, '2025-00001', 'TOR')
  const purpose = `Employment E2E ${uniq()}`
  const ref = await fileRequest(page, /Transcript of Records/, purpose)
  await expect(myRow(page, ref)).toContainText('Pending')

  // Registrar: dashboard approval queue -> review -> approve the request (nothing generated yet).
  const registrar = await pageAs(browser, 'registrar', baseURL!)
  await registrar.goto('/')
  await queueCard(registrar, 'Approval queue').getByRole('link', { name: ref }).click()
  await expect(registrar.getByRole('heading', { level: 1, name: `${ref} · TOR` })).toBeVisible()
  await expect(registrar.getByText(`Purpose: ${purpose}`)).toBeVisible()
  await expect(badge(registrar, 'Pending approval')).toBeVisible()
  await expect(registrar.getByText(/No document yet/)).toBeVisible()
  await registrar.getByRole('button', { name: 'Approve request' }).click()

  // Approval ran retrieval + generation: the document now waits for verification.
  await expect(badge(registrar, 'Ready for verification')).toBeVisible()
  await expect(registrar.getByText(/TOR-\d{4}-\d{6} · \d+ page\(s\)/)).toBeVisible()

  // Student: the request shows as Approved while the registrar checks the document.
  await page.reload()
  await expect(myRow(page, ref)).toContainText('Approved')
  await expect(myRow(page, ref).getByRole('button', { name: 'Download PDF' })).toHaveCount(0)

  // Registrar: verification queue -> verify (auto-release is on by default).
  await registrar.goto('/')
  await expect(queueCard(registrar, 'Approval queue').getByRole('link', { name: ref })).toHaveCount(0)
  await queueCard(registrar, 'Verification queue').getByRole('link', { name: ref }).click()
  await expect(badge(registrar, 'Ready for verification')).toBeVisible()
  await registrar.getByRole('button', { name: 'Verify & release' }).click()
  await expect(badge(registrar, 'Released')).toBeVisible()
  await registrar.goto('/')
  await expect(queueCard(registrar, 'Verification queue').getByRole('link', { name: ref })).toHaveCount(0)
  await registrar.context().close()

  // Student: completed, with a working download link.
  await page.reload()
  await expect(myRow(page, ref)).toContainText('Completed')
  await downloadWorks(page, ref)
})

test('student requests a COE, registrar approves and verifies it from the request list', async ({ page, browser, baseURL }) => {
  // Ana has an INC grade: TOR is refused with a reason, COE is allowed.
  await loginAPI(page, 'ana.garcia')
  await cancelOpenRequests(page, '2025-00003', 'COE')
  await openHome(page)
  await expect(page.getByText(/TOR not available:.*Incomplete grade/)).toBeVisible()
  await expect(page.getByRole('option', { name: /Transcript of Records.*not available/ })).toHaveAttribute('disabled')

  const ref = await fileRequest(page, /Certificate of Enrollment/, `Scholarship E2E ${uniq()}`)

  const registrar = await pageAs(browser, 'registrar', baseURL!)
  await registrar.goto('/requests?status=requested')
  await registrar.getByRole('link', { name: ref }).click()
  await expect(registrar.getByRole('heading', { level: 1, name: `${ref} · COE` })).toBeVisible()
  await registrar.getByRole('button', { name: 'Approve request' }).click()
  await expect(badge(registrar, 'Ready for verification')).toBeVisible()
  await registrar.getByRole('button', { name: 'Verify & release' }).click()
  await expect(badge(registrar, 'Released')).toBeVisible()
  await registrar.context().close()

  await page.reload()
  await expect(myRow(page, ref)).toContainText('Completed')
  await downloadWorks(page, ref)
})

test('registrar rejects a pending request with a reason; nothing is generated', async ({ page, browser, baseURL }) => {
  await loginAPI(page, 'juan.delacruz')
  await cancelOpenRequests(page, '2025-00002', 'COE')
  const ref = await fileRequest(page, /Certificate of Enrollment/, `Visa E2E ${uniq()}`)
  await expect(myRow(page, ref)).toContainText('Pending')

  const registrar = await pageAs(browser, 'registrar', baseURL!)
  await registrar.goto('/requests?status=requested')
  await registrar.getByRole('link', { name: ref }).click()
  await expect(registrar.getByRole('button', { name: 'Reject' })).toBeDisabled() // reason first
  const reason = `Purpose not accepted ${uniq()}`
  await registrar.getByLabel(/^Reason/).fill(reason)
  await registrar.getByRole('button', { name: 'Reject' }).click()
  await expect(badge(registrar, 'Rejected')).toBeVisible()
  await expect(registrar.getByText(`Rejected: ${reason}`)).toBeVisible()
  await expect(registrar.getByText('No document generated for this request.')).toBeVisible()
  await registrar.context().close()

  await page.reload()
  await expect(myRow(page, ref)).toContainText('Rejected')
  await expect(myRow(page, ref)).toContainText(reason)
})
