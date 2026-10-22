import { type Browser, expect, test } from '@playwright/test'

/** The pointer is hidden only on touch-only screens (the station TFT), never with a mouse. */
async function loginButtonCursor(browser: Browser, baseURL: string, touchOnly: boolean) {
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 800, height: 480 },
    // Chromium reports hover: none / pointer: coarse for a mobile touch device.
    ...(touchOnly ? { hasTouch: true, isMobile: true } : {}),
  })
  const page = await context.newPage()
  await page.goto('/login')
  const button = page.getByRole('button', { name: 'Sign in' })
  await expect(button).toBeVisible()
  const cursor = await button.evaluate((el) => getComputedStyle(el).cursor)
  const hover = await page.evaluate(() => matchMedia('(any-hover: none)').matches)
  await context.close()
  return { cursor, touchOnlyDetected: hover }
}

test('touch-only screen hides the pointer; with a mouse it is shown @tft', async ({ browser, baseURL }) => {
  const touch = await loginButtonCursor(browser, baseURL!, true)
  expect(touch.touchOnlyDetected).toBe(true)
  expect(touch.cursor).toBe('none')

  const mouse = await loginButtonCursor(browser, baseURL!, false)
  expect(mouse.touchOnlyDetected).toBe(false)
  expect(mouse.cursor).not.toBe('none')
})
