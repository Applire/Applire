// tests/oq/mobile/cv-review.spec.ts

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
 * Mobile CV review — OQ Tests (US227, 390x844)
 *
 * Covers US226: below `md` the desktop RefinementSidebar is replaced by
 * `MobileCommandBar` — exactly three actions (ATS Checks, Fine-tune,
 * Download PDF). Asserts each opens its bottom sheet (or triggers the
 * download flow) and that the document workspace has no horizontal body
 * overflow at 390px.
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const FLOW_ID = "flow-mobile-cv-0000-0000-0000-00000001";
const CV_ID = "cv-mobile-0000-0000-0000-000000000002";
const JOB_ID = "job-mobile-cv-0000-0000-0000-00000003";

const MOCK_FLOW_STATE = {
  job_id: JOB_ID,
  job_summary: { role_title: "Senior Software Engineer" },
  gap_summary: {
    match_score: 0.87,
    gaps: [{ id: "gap-1", label: "Cloud Experience" }],
    sections: [
      {
        section_id: "intro",
        label: "Introduction",
        content: "Erfahrener Entwickler",
        has_override: false,
        gaps: [{ id: "gap-1", label: "Cloud Experience" }],
      },
    ],
  },
  cv_summary: {
    cv_id: CV_ID,
    pdf_url: `http://localhost:8001/api/cv/${CV_ID}/pdf`,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  },
};

const MOCK_CV_HTML = `<html><body>
  <h1>Max Mustermann</h1>
  <p>Senior Software Engineer</p>
</body></html>`;

const MOCK_ATS_REPORT = {
  report: {
    checks: [
      { id: "contact-name", status: "pass" },
      { id: "skills", status: "pass" },
      { id: "reading-order", status: "fail", details: null },
    ],
    keywords: {
      present: ["TypeScript", "React"],
      missing: ["Kubernetes"],
      missing_claimable: [],
      missing_honest_gap: ["Kubernetes"],
    },
  },
};

// E057/ADR-079 clause 4 groundwork (#629, story #637): a not_applicable
// check must render distinguishably in the mobile ATS sheet and must never
// inflate the pass-count badge. No producer constructs one in a real report
// yet — this is a synthetic fixture, same status as MOCK_ATS_REPORT above.
const MOCK_ATS_REPORT_WITH_NOT_APPLICABLE = {
  report: {
    checks: [
      { id: "contact-name", status: "pass" },
      { id: "skills", status: "pass" },
      {
        id: "page-length",
        status: "not_applicable",
        details: "page count is not defined for this export format",
      },
    ],
    keywords: {
      present: ["TypeScript", "React"],
      missing: [],
      missing_claimable: [],
      missing_honest_gap: [],
    },
  },
};

// US228: ContentTab(variant="sections") is the only consumer of
// GET /api/cv/{id}/sections, and it was never mocked here — the Fine-tune sheet
// therefore rendered ContentTab's LOAD-ERROR state, and the old assertion
// (a degraded notice sitting above it) passed anyway. Mocking it is what makes
// "the editing surface is real on mobile" a claim this lane can actually check.
const MOCK_SECTIONS = {
  sections: [
    {
      section_id: "intro",
      label: "Introduction",
      content: "Erfahrene Senior Software Engineerin mit zwölf Jahren in verteilten Systemen.",
      has_override: false,
      gaps: [],
    },
    {
      section_id: "pos-1",
      label: "Senior Engineer — SAP",
      content: "Migration von 40 Legacy-Diensten nach Kubernetes.",
      has_override: true,
      gaps: [],
    },
  ],
  general_gaps: [],
};

async function setupCvMocks(page: import("@playwright/test").Page) {
  await page.route(`**/api/cv/${CV_ID}/sections`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_SECTIONS) })
  );
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_STATE) })
  );
  await page.route(`**/api/cv/${CV_ID}/html`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: MOCK_CV_HTML })
  );
  await page.route(`**/api/cv/${CV_ID}/ats-report`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ATS_REPORT) })
  );
  await page.route(`**/api/cv/${CV_ID}/profile-diff`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], grounded: true }) })
  );
}

test.describe("Mobile CV review command bar (390x844)", () => {
  test.beforeEach(async ({ page }) => {
    await setupCvMocks(page);
  });

  test("command bar renders with no horizontal overflow", async ({ page }, testInfo) => {
    await page.goto(`/flow/${FLOW_ID}/cv`);

    const commandBar = page.getByTestId("mobile-command-bar");
    await expect(commandBar).toBeVisible({ timeout: 10000 });
    await expect(page.getByTestId("command-ats")).toBeVisible();
    await expect(page.getByTestId("command-finetune")).toBeVisible();
    await expect(page.getByTestId("command-download")).toBeVisible();

    // The desktop RefinementSidebar must NOT take up layout space below md.
    await expect(page.getByTestId("refinement-sidebar")).toBeHidden();

    const overflow = await page.evaluate(() => ({
      scrollWidth: document.body.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);

    await page.screenshot({ path: testInfo.outputPath("cv-review-mobile-390x844.png"), fullPage: true });
    await testInfo.attach("cv-review-mobile-390x844", {
      path: testInfo.outputPath("cv-review-mobile-390x844.png"),
      contentType: "image/png",
    });
  });

  test("ATS Checks opens the sheet with the pass-count badge and report", async ({ page }, testInfo) => {
    await page.goto(`/flow/${FLOW_ID}/cv`);
    await expect(page.getByTestId("mobile-command-bar")).toBeVisible({ timeout: 10000 });

    // Badge reflects the 2 passing checks from the mock report.
    await expect(page.getByTestId("command-ats-badge")).toHaveText("2", { timeout: 5000 });

    await page.getByTestId("command-ats").tap();
    const sheet = page.getByTestId("command-sheet");
    await expect(sheet).toBeVisible({ timeout: 5000 });
    // E058/ADR-081 cl. 7: the sheet now hosts the SAME live ReviewSurface
    // instance as the desktop "review" tab, never a forked panel.
    await expect(sheet.getByTestId("review-surface")).toBeVisible();

    await page.screenshot({ path: testInfo.outputPath("cv-review-ats-sheet.png"), fullPage: true });
    await testInfo.attach("cv-review-ats-sheet", {
      path: testInfo.outputPath("cv-review-ats-sheet.png"),
      contentType: "image/png",
    });

    await page.getByTestId("command-sheet-close").tap();
    await expect(sheet).toBeHidden({ timeout: 5000 });
  });

  // E057/ADR-079 clause 4 groundwork (#629, story #637): the third check
  // state renders distinguishably in the real mobile sheet (not the compact
  // desktop card) and never inflates the pass-count badge.
  test("ATS sheet renders a not_applicable check distinctly and never inflates the pass badge", async ({
    page,
  }, testInfo) => {
    // Overrides the beforeEach mock for this one test — Playwright routes
    // registered later win, so this replaces MOCK_ATS_REPORT for this test only.
    await page.route(`**/api/cv/${CV_ID}/ats-report`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_ATS_REPORT_WITH_NOT_APPLICABLE),
      })
    );

    await page.goto(`/flow/${FLOW_ID}/cv`);
    await expect(page.getByTestId("mobile-command-bar")).toBeVisible({ timeout: 10000 });

    // The badge counts only the 2 genuine passes — a not_applicable check
    // must never inflate it (the exact fold this task exists to prevent).
    await expect(page.getByTestId("command-ats-badge")).toHaveText("2", { timeout: 5000 });

    await page.getByTestId("command-ats").tap();
    const sheet = page.getByTestId("command-sheet");
    await expect(sheet).toBeVisible({ timeout: 5000 });
    // E058/ADR-081 cl. 2/9: the sheet now hosts ReviewSurface, not the compact
    // ATSChecksPanel — a not_applicable check renders as its own group-4 review
    // row (lib/review-groups.ts `buildGroup4`, severity "neutral"), never folded
    // into a pass or a failure. It is the only group with items in this fixture
    // (no ATS/Oracle/critic/cluster findings elsewhere), so overview mode opens
    // it by default — no extra click needed.
    await expect(sheet.getByTestId("review-item-g4-check-na-page-length")).toBeVisible();

    await page.screenshot({
      path: testInfo.outputPath("cv-review-ats-sheet-not-applicable.png"),
      fullPage: true,
    });
    await testInfo.attach("cv-review-ats-sheet-not-applicable", {
      path: testInfo.outputPath("cv-review-ats-sheet-not-applicable.png"),
      contentType: "image/png",
    });

    await page.getByTestId("command-sheet-close").tap();
    await expect(sheet).toBeHidden({ timeout: 5000 });
  });

  // US228 (E040): the Fine-tune sheet used to lead with "editing is optimised
  // for a larger screen — open this CV on your computer". Measured at 390x844
  // and at 390x500 (keyboard-open height), that statement is false: the section
  // editor opens, Save enables only on a real change, the scope prompt covers
  // the whole viewport, and the PATCH lands. The notice is retired, and this
  // asserts its ABSENCE — a leftover would otherwise render as its own key name
  // (next-intl does not throw on a missing key) and no other test would see it.
  test("Fine-tune opens the real editing surface with no degraded disclaimer", async ({ page }) => {
    await page.goto(`/flow/${FLOW_ID}/cv`);
    await expect(page.getByTestId("mobile-command-bar")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("command-finetune").tap();
    const sheet = page.getByTestId("command-sheet");
    await expect(sheet).toBeVisible({ timeout: 5000 });
    await expect(sheet.getByTestId("command-finetune-degraded")).toHaveCount(0);
    await expect(sheet.getByText("Introduction")).toBeVisible({ timeout: 5000 });

    await page.getByTestId("command-sheet-close").tap();
    await expect(sheet).toBeHidden({ timeout: 5000 });
  });

  // US228 — the whole reason the disclaimer could go. Drives edit -> save at
  // 390 px: the editor opens, Save is inert until something actually changed,
  // the scope prompt covers the VIEWPORT (it is `fixed` and not portalled, so a
  // transform-trapped ancestor would dim only the sheet), and the PATCH lands.
  test("a section can be edited and saved from the phone", async ({ page }, testInfo) => {
    let patched: { method: string; body: string | null } | null = null;
    await page.route(`**/api/cv/${CV_ID}/sections/*`, (route) => {
      patched = { method: route.request().method(), body: route.request().postData() };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ html: MOCK_CV_HTML, overrides_applied: ["intro"], resolved_gaps: [] }),
      });
    });

    await page.goto(`/flow/${FLOW_ID}/cv`);
    await expect(page.getByTestId("mobile-command-bar")).toBeVisible({ timeout: 10000 });
    await page.getByTestId("command-finetune").tap();
    const sheet = page.getByTestId("command-sheet");
    await expect(sheet).toBeVisible({ timeout: 5000 });

    await sheet.getByText("Introduction").first().tap();
    const textarea = page.getByTestId("section-textarea");
    await expect(textarea).toBeVisible({ timeout: 5000 });

    // Inert until a real change — the guard against a fat-fingered save.
    await expect(page.getByTestId("section-save")).toBeDisabled();
    await textarea.tap();
    await textarea.fill("Auf dem Handy bearbeitet — zwölf Jahre in verteilten Systemen.");
    await expect(page.getByTestId("section-save")).toBeEnabled();

    // The action row must be IN the fold, not merely reachable by scrolling.
    // Checked at the height a 844 px phone actually leaves while its keyboard is
    // up: `vh` does not shrink for the keyboard, so a Save button below the fold
    // at typing time is a Save button behind the keyboard. Measured before
    // US228 at this height: y=540 in a 500 px viewport, i.e. entirely off-screen.
    await page.setViewportSize({ width: 390, height: 500 });
    const save = await page.getByTestId("section-save").boundingBox();
    expect(save).not.toBeNull();
    expect(save!.y + save!.height, "Save row sits behind the keyboard").toBeLessThanOrEqual(500);
    await page.setViewportSize({ width: 390, height: 844 });

    await page.getByTestId("section-save").tap();
    const scopeButton = page.getByTestId("save-cv-only-btn");
    await expect(scopeButton).toBeVisible({ timeout: 5000 });
    const overlay = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="save-cv-only-btn"]')?.closest(".fixed");
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { width: Math.round(r.width), height: Math.round(r.height) };
    });
    expect(overlay).toEqual({ width: 390, height: 844 });

    await page.screenshot({ path: testInfo.outputPath("cv-finetune-save-scope.png"), fullPage: true });
    await testInfo.attach("cv-finetune-save-scope", {
      path: testInfo.outputPath("cv-finetune-save-scope.png"),
      contentType: "image/png",
    });

    await scopeButton.tap();
    await expect.poll(() => patched?.method, { timeout: 5000 }).toBe("PATCH");
    expect(patched!.body).toContain("Auf dem Handy bearbeitet");
    // Saved means saved: the unsaved flag clears and Save goes inert again.
    await expect(page.getByTestId("section-save")).toBeDisabled({ timeout: 5000 });
  });

  test("Download PDF triggers the pre-download notice then the download", async ({ page }) => {
    let pdfFetched = false;
    await page.route(`**/api/cv/${CV_ID}/pdf`, (route) => {
      pdfFetched = true;
      route.fulfill({
        status: 200,
        contentType: "application/pdf",
        headers: { "Content-Disposition": 'attachment; filename="lebenslauf-test.pdf"' },
        body: Buffer.from("%PDF-1.4 mock content"),
      });
    });

    await page.goto(`/flow/${FLOW_ID}/cv`);
    await expect(page.getByTestId("mobile-command-bar")).toBeVisible({ timeout: 10000 });

    await page.getByTestId("command-download").tap();
    const notice = page.getByTestId("predownload-notice");
    await expect(notice).toBeVisible({ timeout: 10000 });

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByTestId("predownload-download").tap(),
    ]);

    expect(download).toBeTruthy();
    expect(pdfFetched).toBe(true);
  });
});
