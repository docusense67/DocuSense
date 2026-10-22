import { expect, test } from '@playwright/test'
import { email, loginAPI, loginUI } from './support'

const ROLES = [
  { handle: 'admin', dashboard: 'Accounts & stations', role: 'System Admin' },
  { handle: 'registrar', dashboard: 'Registrar dashboard', role: 'Registrar' },
  { handle: 'teacher', dashboard: 'My classes', role: 'Teacher' },
  { handle: 'maria.santos', dashboard: 'My documents', role: 'Student' },
]

test.describe('sign in and out', () => {
  for (const { handle, dashboard, role } of ROLES) {
    test(`${handle} lands on "${dashboard}" and can sign out`, async ({ page }) => {
      await loginUI(page, handle)
      await expect(page.getByRole('heading', { level: 1, name: dashboard })).toBeVisible()
      await expect(page.getByText(`${email(handle)} · ${role}`)).toBeVisible()

      await page.getByRole('button', { name: 'Sign out' }).click()
      await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
      // The session is gone server-side too, not just in the UI.
      expect((await page.request.get('/api/auth/me')).status()).toBe(401)
    })
  }

  test('wrong credentials show the generic error', async ({ page }) => {
    await page.goto('/')
    // Unknown account, so no real account's failed-login counter moves.
    await page.getByLabel('Email').fill('nobody@school.edu.ph')
    await page.getByLabel('Password').fill('not-the-password')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toContainText('Invalid email or password')
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  })
})

test.describe('role boundaries (smoke; full matrix is in backend/tests/test_access_matrix.py)', () => {
  test('a student cannot open registrar pages', async ({ page }) => {
    await loginAPI(page, 'maria.santos')
    for (const path of ['/requests', '/records', '/scan', '/audit', '/templates']) {
      await page.goto(path)
      await expect(page).toHaveURL(/\/$/) // unknown routes for this role redirect home
      await expect(page.getByRole('heading', { level: 1, name: 'My documents' })).toBeVisible()
    }
    const nav = page.getByRole('navigation')
    await expect(nav.getByRole('link')).toHaveText(['My documents', 'Application history', 'My adviser'])
    expect((await page.request.get('/api/dashboard/registrar')).status()).toBe(403)
    expect((await page.request.get('/api/scan-stations')).status()).toBe(403)
  })

  test('only the validation operator sees the scan station', async ({ page }) => {
    await loginAPI(page, 'teacher')
    await page.goto('/scan')
    await expect(page.getByRole('heading', { level: 1, name: 'My classes' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Scan station' })).toHaveCount(0)
    expect((await page.request.get('/api/scan-stations')).status()).toBe(403)
  })
})
