// tests/oq/mobile/gaps-triage.spec.ts

// Copyright (C) 2024-2026 Tobias Rosenbaum
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

import { test, expect } from "@playwright/test";

/**
 * Mobile gap triage — OQ Tests (US227, 390x844)
 *
 * Covers US225: below `md` the interview/generate/explore decision cluster
 * becomes a `fixed` bottom bar (`gaps-decision-bar`) so it stays reachable
 * without scrolling to the end of the page. Asserts the bar (and its primary
 * actions) is visible and tappable within the 390px viewport, and that the
 * shell has no horizontal body overflow.
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const FLOW_ID = "flow-mobile-gaps-0000-0000-000000000001";
const JOB_ID = "job-mobile-gaps-0000-0000-000000000002";
const GAP_ID = "gap-mobile-gaps-0000-0000-000000000003";

const MOCK_FLOW_STATE = {
  job_id: JOB_ID,
  user_type: "new",
  available_actions: {},
  gap_summary: { gap_analysis_id: GAP_ID },
  job_summary: { role_title: "Senior Software Engineer" },
};

const MOCK_GAP_ANALYSIS = {
  id: GAP_ID,
  match_score: 0.72,
  category_a: ["Python", "FastAPI"],
  category_b: ["Docker"],
  category_c: ["Kubernetes"],
  strengths: ["Python", "FastAPI"],
  gap_clusters: [
    {
      id: "cluster-mobile-c-0000-0000-000000000005",
      label: "Container Orchestration",
      category: "C",
      gaps: ["Kubernetes"],
      jd_skills: ["Kubernetes"],
      jd_context: "Required for production deployments",
    },
  ],
};

const MOCK_PROFILE = {
  positions_count: 5,
  projects_count: 12,
  certifications_count: 3,
  data_points_count: 47,
};

async function setupGapsMocks(page: import("@playwright/test").Page) {
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_STATE) })
  );
  await page.route(`**/api/job/${JOB_ID}/gaps`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) })
  );
  await page.route("**/api/profile", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_PROFILE) })
  );
}

test.describe("Mobile gap triage (390x844)", () => {
  test.beforeEach(async ({ page }) => {
    await setupGapsMocks(page);
  });

  test("decision bar is fixed, visible and tappable; no horizontal overflow", async ({ page }, testInfo) => {
    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    const decisionBar = page.getByTestId("gaps-decision-bar");
    await expect(decisionBar).toBeVisible();

    // Fixed at the viewport bottom (US225): its bottom edge must sit at (or
    // very near) the 844px viewport height, not wherever the document flow
    // would otherwise have placed it.
    const barBox = await decisionBar.boundingBox();
    expect(barBox).not.toBeNull();
    expect(barBox!.y + barBox!.height).toBeGreaterThan(800);

    // Primary actions are visible and within the tappable viewport.
    const interviewButton = page.getByTestId("interview-button");
    await expect(interviewButton).toBeVisible();
    const interviewBox = await interviewButton.boundingBox();
    expect(interviewBox).not.toBeNull();
    expect(interviewBox!.x).toBeGreaterThanOrEqual(0);
    expect(interviewBox!.x + interviewBox!.width).toBeLessThanOrEqual(390);

    const generateButton = page.getByTestId("generate-cv-button");
    await expect(generateButton).toBeVisible();

    // No horizontal body overflow at 390px (verification-hierarchy: real
    // pixels, not the a11y tree).
    const overflow = await page.evaluate(() => ({
      scrollWidth: document.body.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);

    // Evidence artifact (verification-hierarchy: real screenshot, not the a11y
    // snapshot) — written to test-results/ AND embedded in the HTML report.
    await page.screenshot({ path: testInfo.outputPath("gaps-mobile-390x844.png"), fullPage: true });
    await testInfo.attach("gaps-mobile-390x844", {
      path: testInfo.outputPath("gaps-mobile-390x844.png"),
      contentType: "image/png",
    });
  });

  test("tapping Generate CV Now advances the flow and navigates to the CV page", async ({ page }) => {
    let advanceCalled = false;
    await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) => {
      advanceCalled = true;
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
    });

    await page.goto(`/flow/${FLOW_ID}/gaps`);
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("generate-cv-button").tap();

    await expect(page).toHaveURL(`/flow/${FLOW_ID}/cv`, { timeout: 10000 });
    expect(advanceCalled).toBe(true);
  });
});
