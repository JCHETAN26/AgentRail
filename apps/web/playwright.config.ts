import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end configuration.
 *
 * These specs drive a real browser against a running stack (`make compose-up`
 * plus the API, worker and sandbox). They assert the resulting state, not that
 * a process started.
 */
const externalBaseURL = process.env.AGENTRAIL_WEB_BASE_URL;
const baseURL = externalBaseURL ?? 'http://localhost:3000';

/**
 * When AGENTRAIL_WEB_BASE_URL is set the web app is already running (Compose,
 * or a deployed environment) and Playwright must not start its own. Otherwise
 * it boots the production build locally.
 */
const webServer = externalBaseURL
  ? {}
  : {
      webServer: {
        command: 'pnpm start',
        url: baseURL,
        timeout: 120_000,
        reuseExistingServer: !process.env.CI,
      },
    };

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  ...webServer,
});
