import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end configuration.
 *
 * The web app runs on 3737, not Next.js's default 3000, and Playwright always
 * starts its own server rather than reusing one.
 *
 * Both of those are scar tissue. `reuseExistingServer: true` attaches to
 * whatever is already listening — which, on a machine running another project,
 * meant the entire suite ran against an unrelated application and failed with a
 * baffling "cannot find the Email field". Choosing a rarer port reduces the
 * chance of a collision; refusing to reuse is what makes a collision *loud*
 * instead of silently wrong.
 *
 * These specs drive a real browser against a running stack (`make compose-up`
 * plus the API, worker and sandbox). They assert the resulting state, not that
 * a process started.
 */
const externalBaseURL = process.env.AGENTRAIL_WEB_BASE_URL;
const baseURL = externalBaseURL ?? `http://localhost:${process.env.AGENTRAIL_WEB_PORT ?? '3737'}`;

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
        // Never adopt a stranger's server. If the port is taken, fail loudly.
        reuseExistingServer: false,
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
