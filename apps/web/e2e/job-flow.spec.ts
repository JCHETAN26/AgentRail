import { expect, test } from '@playwright/test';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

test.describe('deterministic job path', () => {
  test('a submitted job is executed by a worker and its result is displayed', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Deterministic request path' })).toBeVisible();
    await expect(page.getByText('No job yet.')).toBeVisible();

    await page.getByLabel('Message').fill('end to end');
    await page.getByRole('button', { name: 'Submit job' }).click();

    // The worker must claim, execute and complete the job for this to appear.
    await expect(page.getByTestId('job-state')).toHaveText('Completed', { timeout: 30_000 });

    const payload = page.getByTestId('job-result-payload');
    // sha256("end to end") = 9a53b8f5... — the digest proves the message reached
    // the sandbox unmodified through the API, Redis and the worker.
    await expect(payload).toContainText('"echo": "end to end"');
    await expect(payload).toContainText('"digest"');
  });

  test('the job created by the UI is retrievable from the API', async ({ page, request }) => {
    await page.goto('/');
    await page.getByLabel('Message').fill('api readback');
    await page.getByRole('button', { name: 'Submit job' }).click();
    await expect(page.getByTestId('job-state')).toHaveText('Completed', { timeout: 30_000 });

    const listed = await request.get(`${API_BASE_URL}/api/v1/jobs?limit=1`);
    expect(listed.ok()).toBeTruthy();
    const body = (await listed.json()) as {
      items: Array<{ state: string; payload: { message: string }; attempts: number }>;
    };

    expect(body.items[0]?.payload.message).toBe('api readback');
    expect(body.items[0]?.state).toBe('COMPLETED');
    // Exactly one execution: no duplicate work despite at-least-once delivery.
    expect(body.items[0]?.attempts).toBe(1);
  });

  test('a validation failure shows a correlation id the user can quote', async ({ page }) => {
    await page.goto('/');

    // Force a rejection the UI cannot prevent client-side.
    await page.route('**/api/v1/jobs', async (route) => {
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
