import { expect, test, type Page } from '@playwright/test';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

/**
 * Sign in and make sure the workspace has a project to run jobs in.
 *
 * A new account lands on the "create your organisation" form; a returning one
 * lands straight on the job launcher. Waiting for *either* to appear before
 * branching is what makes this deterministic — an immediate `isVisible()` check
 * races the render and silently takes the wrong branch.
 */
async function signIn(page: Page, email: string, organisationName: string): Promise<void> {
  await page.goto('/');
  await page.getByLabel('Email').fill(email);
  await page.getByRole('button', { name: 'Continue' }).click();

  await expect(page.getByTestId('identity')).toContainText(email);

  const createOrganisation = page.getByRole('heading', { name: 'Create your organisation' });
  const runAJob = page.getByRole('heading', { name: 'Run a job' });
  await expect(createOrganisation.or(runAJob)).toBeVisible();

  if (await createOrganisation.isVisible()) {
    await page.getByLabel('Organisation name').fill(organisationName);
    await page.getByRole('button', { name: 'Create organisation' }).click();
  }

  await expect(runAJob).toBeVisible();
}

test.describe('authentication', () => {
  test('an anonymous visitor is asked to sign in', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Run a job' })).toBeHidden();
  });

  test('signing in reveals the workspace, and signing out hides it again', async ({ page }) => {
    await signIn(page, `e2e-${Date.now()}@example.com`, 'E2E Labs');

    await page.getByRole('button', { name: 'Sign out' }).click();

    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Run a job' })).toBeHidden();
  });

  test('the API rejects an unauthenticated job listing', async ({ request }) => {
    const response = await request.get(`${API_BASE_URL}/api/v1/organisations`);

    expect(response.status()).toBe(401);
    expect((await response.json()).code).toBe('unauthenticated');
  });
});

test.describe('deterministic job path', () => {
  test('a submitted job is executed by a worker and its result is displayed', async ({ page }) => {
    await signIn(page, `e2e-${Date.now()}@example.com`, 'E2E Labs');

    await expect(page.getByRole('heading', { name: 'Deterministic request path' })).toBeVisible();
    await expect(page.getByText('No job yet.')).toBeVisible();

    await page.getByLabel('Message').fill('end to end');
    await page.getByRole('button', { name: 'Submit job' }).click();

    // The worker must claim, execute and complete the job for this to appear.
    await expect(page.getByTestId('job-state')).toHaveText('Completed', { timeout: 30_000 });

    const payload = page.getByTestId('job-result-payload');
    // The digest proves the message reached the sandbox unmodified through the
    // API, Redis and the worker.
    await expect(payload).toContainText('"echo": "end to end"');
    await expect(payload).toContainText('"digest"');
  });

  test('a validation failure shows a correlation id the user can quote', async ({ page }) => {
    await signIn(page, `e2e-${Date.now()}@example.com`, 'E2E Labs');

    // Force a rejection the UI cannot prevent client-side.
    await page.route('**/api/v1/projects/*/jobs', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 422,
        contentType: 'application/json',
        headers: { 'x-correlation-id': 'cid_e2e_forced' },
        body: JSON.stringify({
          code: 'validation_failed',
          message: 'The request failed validation.',
          correlation_id: 'cid_e2e_forced',
          details: {},
        }),
      });
    });

    await page.getByLabel('Message').fill('rejected');
    await page.getByRole('button', { name: 'Submit job' }).click();

    const notice = page.getByTestId('error-notice');
    await expect(notice).toBeVisible();
    await expect(notice).toContainText('cid_e2e_forced');
  });
});

test.describe('tenant isolation', () => {
  test('a second tenant cannot see the first tenant’s jobs', async ({ page, browser }) => {
    const first = `e2e-a-${Date.now()}@example.com`;
    await signIn(page, first, 'Tenant A');
    await page.getByLabel('Message').fill('tenant a secret');
    await page.getByRole('button', { name: 'Submit job' }).click();
    await expect(page.getByTestId('job-state')).toHaveText('Completed', { timeout: 30_000 });

    // A completely separate browser context: different cookies, different user.
    const otherContext = await browser.newContext();
    const otherPage = await otherContext.newPage();
    try {
      await signIn(otherPage, `e2e-b-${Date.now()}@example.com`, 'Tenant B');

      await expect(otherPage.getByText('No job yet.')).toBeVisible();
      await expect(otherPage.locator('body')).not.toContainText('tenant a secret');
    } finally {
      await otherContext.close();
    }
  });
});
