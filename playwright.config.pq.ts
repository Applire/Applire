import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright Configuration — PQ tier (persona journey tests)
 *
 * Runs full persona journey tests (tests/pq/). Uses the CI Docker stack
 * (LLM_PROVIDER=mock) — no API key required.
 * Runs automatically in CI after IQ and OQ tiers pass.
 * For manual runs: docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d
 */
export default defineConfig({
  testDir: './tests/pq',
  globalSetup: './tests/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 120 * 1000,
  expect: { timeout: 15 * 1000 },
  reporter: [['html'], ['github']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15 * 1000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  retries: 1,
  outputDir: '/tmp/test-results-pq',
});
