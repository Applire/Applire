// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

// The operator's version-jump notice on the dashboard (US310 / #687, ADR-087).
//
// Everything here is stubbed at `/health`, because that is the component's only
// input: the comparison itself runs once in the backend lifespan and the frontend
// renders what it reports. The four properties worth pinning are the four the
// story argues about — a quiet day renders NOTHING, an upgrade renders both
// lists, dismissing calls the endpoint that records the running version as seen,
// and the debug-log warning is NOT dismissable because it is a live posture
// rather than an event.

import { test, expect } from "@playwright/test";

const QUIET_HEALTH = {
  status: "ok",
  edition: "community",
  version: "0.42.0-beta",
  llm_provider: "mock",
  upgrade_notice: null,
  debug_log_on: false,
  topology: "production",
};

const NOTICE_HEALTH = {
  ...QUIET_HEALTH,
  upgrade_notice: {
    from: "0.40.0-beta",
    to: "0.42.0-beta",
    unset: [
      {
        env_var: "POSTGRES_PASSWORD",
        introduced_in: "0.42.0",
        default: "applire",
        description: "Database password.",
      },
    ],
    re_meant: [
      {
        env_var: "INTERVIEW_MAX_QUESTIONS_TARGETED",
        semantics_changed_in: "0.41.0",
        default: "30",
        description: "Interview question-count cap.",
      },
    ],
  },
};

/** Stub everything the dashboard fetches, with `/health` under the test's control. */
async function stubDashboard(page: import("@playwright/test").Page, health: unknown) {
  await page.route("**/health", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(health),
    })
  );
  await page.route("**/api/applications", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [] }),
    })
  );
  await page.route("**/api/profile", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ profile: { personal_info: { name: "Test Operator" } } }),
    })
  );
  await page.route("**/api/profile/import-jobs**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );
}

test.describe("Version-jump notice (US310)", () => {
  test("renders nothing when there is nothing to report", async ({ page }) => {
    await stubDashboard(page, QUIET_HEALTH);
    const healthAnswered = page.waitForResponse((r) => r.url().includes("/health"));
    await page.goto("/dashboard");
    // Two things make this assertion non-vacuous: the dashboard's own <h1> is on
    // screen (so the page really rendered), and /health has answered (so the
    // notice is absent because there is nothing to report, not because the fetch
    // had not come back yet).
    await expect(page.locator("main h1")).toBeVisible();
    await healthAnswered;
    await expect(page.getByTestId("upgrade-notice")).toHaveCount(0);
  });

  test("names the versions, the settings not set here, and the ones that changed meaning", async ({
    page,
  }) => {
    await stubDashboard(page, NOTICE_HEALTH);
    await page.goto("/dashboard");

    const notice = page.getByTestId("upgrade-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("0.40.0-beta");
    await expect(notice).toContainText("0.42.0-beta");
    // The variable NAMES are what an operator acts on — assert both lists by name,
    // not by count, so a rendering that dropped one list still fails.
    await expect(notice).toContainText("POSTGRES_PASSWORD");
    await expect(notice).toContainText("INTERVIEW_MAX_QUESTIONS_TARGETED");
    await expect(notice).toContainText("0.41.0");
  });

  test("dismissing posts to the endpoint that records the running version as seen", async ({
    page,
  }) => {
    await stubDashboard(page, NOTICE_HEALTH);
    let dismissed = 0;
    await page.route("**/api/settings/upgrade-notice/dismiss", (route) => {
      dismissed += 1;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ last_seen_version: "0.42.0-beta", upgrade_notice: null }),
      });
    });
    await page.goto("/dashboard");

    await expect(page.getByTestId("upgrade-notice")).toBeVisible();
    await page.getByTestId("upgrade-notice-dismiss").click();
    await expect(page.getByTestId("upgrade-notice")).toHaveCount(0);
    expect(dismissed).toBe(1);
  });

  test("the debug-log warning appears on its own and offers no dismiss control", async ({
    page,
  }) => {
    // A live posture, not an event: the log is writing CV PII right now, and the
    // way to make the warning go away is to turn the log off.
    await stubDashboard(page, { ...QUIET_HEALTH, debug_log_on: true });
    await page.goto("/dashboard");

    await expect(page.getByTestId("upgrade-notice-debug-log")).toBeVisible();
    await expect(page.getByTestId("upgrade-notice-dismiss")).toHaveCount(0);
  });
});
