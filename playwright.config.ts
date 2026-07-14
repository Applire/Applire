import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright Configuration — IQ + OQ tiers
 *
 * Runs installation checks (tests/iq/) and operational feature tests (tests/oq/).
 * Uses the CI Docker stack (LLM_PROVIDER=mock) — no API key required.
 * For full persona journey tests, use: npx playwright test --config=playwright.config.pq.ts
 */

export default defineConfig({
  testDir: './tests',
  testMatch: ['**/iq/**/*.spec.ts', '**/oq/**/*.spec.ts'],
  globalSetup: './tests/global-setup.ts',

  /**
   * Test execution settings
   */
  fullyParallel: false,
  workers: 1, // Run tests serially to avoid race conditions with shared backend state
  timeout: 60 * 1000, // 60 seconds per test (includes LLM processing)
  expect: {
    timeout: 10 * 1000, // 10 seconds for assertions
  },

  /**
   * Reporter configuration
   * - 'html': Generates detailed HTML report
   * - 'github': GitHub Actions reporter (only active in CI)
   */
  reporter: [
    ['html'],
    ...(process.env.CI ? [['github'] as ['github']] : []), // Add GitHub reporter only in CI
  ],

  /**
   * Shared settings for all projects
   */
  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry', // Capture trace only when a test is retried
    screenshot: 'only-on-failure', // Capture screenshots only on failure
    video: 'retain-on-failure', // Record video only on failure
    actionTimeout: 10 * 1000, // 10 seconds for user actions (click, type, etc.)
  },

  /**
   * Test projects
   * Currently using Chromium only. Can be extended to include Firefox, WebKit later.
   */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      // US227 review finding: the mobile-chromium project below scopes itself
      // to tests/oq/mobile/ via its own testMatch, but this project still
      // inherits the global testMatch (**/oq/**/*.spec.ts) — without this
      // testIgnore it would run every mobile spec a SECOND time, serially
      // (workers: 1), just at the desktop viewport. Excludes ONLY oq/mobile/ —
      // desktop coverage of every other OQ spec is unchanged.
      testIgnore: ['**/oq/mobile/**'],
    },

    // US227 (E040 ADR-050 §6): mobile-viewport OQ lane — 390x844 (iPhone-class
    // small screen), touch-enabled, mobile Chrome UA. Scoped to its own
    // directory via testMatch so it never doubles up with the desktop project
    // above (which explicitly ignores tests/oq/mobile/).
    {
      name: 'mobile-chromium',
      testMatch: ['**/oq/mobile/**/*.spec.ts'],
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
        userAgent:
          'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36',
      },
    },

    // Uncomment to add WebKit testing in the future
    // {
    //   name: 'webkit',
    //   use: { ...devices['Desktop Safari'] },
    // },
  ],

  /**
   * Web Server Configuration (for local development)
   * Commented out because in local development, the user builds/runs docker-compose manually.
   * In CI/CD, the workflow starts the Docker container via docker-compose.
   * 
   * Uncomment and adjust if you want Playwright to manage the server lifecycle:
   */
  // webServer: {
  //   command: 'docker-compose up',
  //   port: 3000,
  //   reuseExistingServer: !process.env.CI,
  //   timeout: 120 * 1000, // 2 minutes to start
  // },

  /**
   * Global configuration for retries
   * - CI environment (GitHub Actions): 2 retries
   * - Local development: 0 retries (faster feedback)
   */
  retries: process.env.CI ? 2 : 0,

  /**
   * Global configuration for parallel execution
   * Set to 1 worker because tests share backend state (same user/flow context)
   * Running in parallel could cause race conditions.
   */

  /**
   * Output directories
   */
  outputDir: './test-results', // Test artifacts (videos, screenshots, traces)
});

/**
 * Usage:
 *
 * Local Development:
 *   npm install
 *   npx playwright install
 *   docker-compose up -d (from Solution/)
 *   npx playwright test
 *   npx playwright test --ui (interactive mode)
 *   npx playwright test --debug (debug mode)
 *
 * GitHub Actions CI/CD:
 *   - Configured in .github/workflows/test.yml
 *   - Automatically installs dependencies and runs tests
 *   - Reports results to GitHub PR checks
 *
 * Debugging:
 *   npx playwright codegen http://localhost:3000 (record new test)
 *   npx playwright test --headed (run with browser visible)
 *   npx playwright test --debug (step through test)
 */
