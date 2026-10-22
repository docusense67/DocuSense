import AxeBuilder from '@axe-core/playwright'
import { type Page, expect, test } from '@playwright/test'
import { loginAPI } from './support'

/** Accessibility smoke: fail only on critical axe violations (serious ones are reported). */
async function criticalViolations(page: Page) {
  const { violations } = await new AxeBuilder({ page }).analyze()
  for (const v of violations.filter((x) => x.impact !== 'critical')) {
    test.info().annotations.push({ type: `axe ${v.impact}`, description: `${v.id}: ${v.help} (${v.nodes.length})` })
  }
  return violations.filter((v) => v.impact === 'critical').map((v) => `${v.id}: ${v.help}`)
}

test('login page has no critical accessibility violations', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  expect(await criticalViolations(page)).toEqual([])
})

test('student dashboard has no critical accessibility violations', async ({ page }) => {
  await loginAPI(page, 'maria.santos')
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'My records' })).toBeVisible()
  await expect(page.getByText('Loading…')).toHaveCount(0)
  expect(await criticalViolations(page)).toEqual([])
})

test('registrar dashboard has no critical accessibility violations', async ({ page }) => {
  await loginAPI(page, 'registrar')
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Registrar dashboard' })).toBeVisible()
  expect(await criticalViolations(page)).toEqual([])
})
