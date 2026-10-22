import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests: the real API (backend/scripts/dev_server.py, throwaway SQLite, demo data)
 * behind the Vite dev server, driven by Chromium. See docs/test-plan.md, "End-to-end tests".
 *
 *   npm run e2e        all projects (desktop Chromium + TFT viewport)
 *   npm run e2e:tft    only @tft tests at the scan station's TFT size
 *   npm run e2e:ui     Playwright UI mode
 *
 * TFT size: TFT_WIDTH / TFT_HEIGHT (CSS pixels), default 800x480. Change the defaults below
 * once the real panel is known.
 */
const TFT_WIDTH = Number(process.env.TFT_WIDTH ?? 800)
const TFT_HEIGHT = Number(process.env.TFT_HEIGHT ?? 480)

const CI = !!process.env.CI
const BACKEND = resolve(import.meta.dirname, '../backend')
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 5173)
const BASE_URL = `http://localhost:${WEB_PORT}`

// Backend interpreter: E2E_PYTHON, else the backend venv (Windows or POSIX layout), else `python`
// (CI installs the backend into the setup-python interpreter).
const VENV_PYTHON = [join(BACKEND, '.venv/Scripts/python.exe'), join(BACKEND, '.venv/bin/python')]
  .find((p) => existsSync(p))
const PYTHON = process.env.E2E_PYTHON ?? VENV_PYTHON ?? 'python'

// A fresh SQLite DB + storage dir for every run the API is started by Playwright. Set once in
// the runner process; workers inherit the env, so they don't create directories of their own.
process.env.E2E_DATA_DIR ??= mkdtempSync(join(tmpdir(), 'adgvs-e2e-'))
const DATA_DIR = process.env.E2E_DATA_DIR

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  workers: CI ? 2 : undefined,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      // The scan station's small TFT LCD (the tablet used in development only mimics it).
      name: 'tft',
      grep: /@tft/,
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: TFT_WIDTH, height: TFT_HEIGHT },
      },
    },
  ],
  webServer: [
    {
      command: `"${PYTHON}" scripts/dev_server.py`,
      cwd: BACKEND,
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: !CI,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
      env: {
        ...process.env,
        ADGVS_DATABASE_URL: `sqlite:///${join(DATA_DIR, 'e2e.db').replaceAll('\\', '/')}`,
        ADGVS_STORAGE_DIR: join(DATA_DIR, 'storage'),
        ADGVS_COOKIE_SECURE: 'false',
      } as Record<string, string>,
    },
    {
      command: `npm run dev -- --port ${WEB_PORT} --strictPort`,
      url: BASE_URL,
      reuseExistingServer: !CI,
      timeout: 120_000,
      stdout: 'ignore',
      stderr: 'pipe',
    },
  ],
})
