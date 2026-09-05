// tests/e2e/oq/cv-section-editor.spec.ts
import { test, expect } from "@playwright/test";

/**
 * CV Section Editor — OQ Tests (Sprint 22 layout)
 *
 * Covers:
 *  - RefinementPanel Content tab: Browse mode shows gap cards + section list
 *  - Clicking a section transitions to Edit mode with SectionEditor textarea
 *  - Edit → Save → preview iframe refreshes
 *  - Clicking a gap card navigates to owning section with gap pre-selected
 *  - Unsaved-changes guard (window.confirm) on browser navigation
 *  - KaileChat rewrite: submit → suggestion appears → Apply
 *
 * Uses page.route() mocks — does NOT require a running backend.
 */

const TEST_FLOW_ID = "ffffffff-ffff-ffff-ffff-ffffffffffff";
const TEST_CV_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const TEST_JOB_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const CV_PAGE_URL = `/flow/${TEST_FLOW_ID}/cv`;

// ---------------------------------------------------------------------------
// Mock data fixtures
// ---------------------------------------------------------------------------

const MOCK_FLOW_STATE = {
  job_id: TEST_JOB_ID,
  job_summary: { role_title: "Senior Software Engineer" },
  gap_summary: {
    match_score: 0.85,
    gaps: [{ id: "Python", label: "Python" }],
    sections: [
      {
        section_id: "introduction",
        label: "Introduction",
        content: "Erfahrener Entwickler mit fünf Jahren Erfahrung.",
        has_override: false,
        gaps: [{ id: "Python", label: "Python" }],
      },
      {
        section_id: "skills",
        label: "Skills",
        content: "Java, Spring Boot",
        has_override: false,
        gaps: [],
      },
    ],
  },
  cv_summary: {
    cv_id: TEST_CV_ID,
    pdf_url: `http://localhost:8001/api/cv/${TEST_CV_ID}/pdf`,
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  },
};

const MOCK_CV_HTML = `<html><body>
  <h1>Max Mustermann</h1>
  <p>Senior Software Engineer</p>
  <p id="intro">Erfahrener Entwickler mit fünf Jahren Erfahrung.</p>
</body></html>`;

const MOCK_CV_HTML_UPDATED = `<html><body>
  <h1>Max Mustermann</h1>
  <p>Senior Software Engineer</p>
  <p id="intro">My edited introduction text for E2E test</p>
</body></html>`;

const MOCK_CV_SECTIONS = {
  sections: [
    {
      section_id: "introduction",
      label: "Introduction",
      content: "Erfahrener Entwickler mit fünf Jahren Erfahrung.",
      has_override: false,
      gaps: [{ id: "Python", label: "Python" }],
    },
    {
      section_id: "skills",
      label: "Skills",
      content: "Java, Spring Boot",
      has_override: false,
      gaps: [],
    },
  ],
  general_gaps: [],
};

/**
 * Click a section button in Browse mode.
 * Uses a button role selector since sections are styled as buttons,
 * avoiding the strict-mode violation of getByText() matching both
 * a button and a heading.
 */
async function clickSectionButton(page: import("@playwright/test").Page, label: string) {
  const btn = page.getByRole("button", { name: label });
  await btn.waitFor({ state: "visible", timeout: 10_000 });
  await btn.click();
}

/**
 * Accept the unsaved-changes window.confirm() dialog that ContentTab triggers.
 * Playwright catches it and auto-accepts.
 */
async function acceptConfirm(page: import("@playwright/test").Page) {
  page.once("dialog", async (dialog) => {
    await dialog.accept();
  });
}

// ---------------------------------------------------------------------------
// Core: Browse -> Edit -> Save
// ---------------------------------------------------------------------------

test.describe("CV Section Editor — Browse/Edit/Save", () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_FLOW_STATE),
      });
    });

    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: MOCK_CV_HTML,
      });
    });

    // GET /api/cv/{id}/sections — fetched by ContentTab to populate section list
    await page.route(`**/api/cv/${TEST_CV_ID}/sections`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_CV_SECTIONS),
        });
      } else {
        await route.continue();
      }
    });

    await page.route(
      `**/api/cv/${TEST_CV_ID}/sections/**`,
      async (route) => {
        if (route.request().method() === "PATCH") {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              html: MOCK_CV_HTML_UPDATED,
              overrides_applied: ["introduction"],
            }),
          });
        } else if (route.request().method() === "POST" && route.request().url().includes("/rewrite")) {
          await route.continue();
        } else {
          await route.continue();
        }
      }
    );
  });

  // -------------------------------------------------------------------------
  // Refinement panel visible with Browse content
  // -------------------------------------------------------------------------

  test("Edit tab loads with section list and per-section gap counts", async ({
    page,
  }) => {
    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: "review" is now the default tab, and the section
    // editor (ContentTab, mounted with variant="sections" on this page) lives
    // on "edit". The aggregate "N gap(s) found for <role>" summary that used
    // to render here only exists for variant="full" — it no longer renders on
    // this page at all (that finding moved into the review surface's groups) —
    // so the closest surviving equivalent is the per-section gap-count badge
    // that variant="sections" still renders next to each section row.
    await page.getByTestId("sidebar-tab-edit").click();

    // Section list buttons (they're rendered as buttons)
    const introBtn = page.getByRole("button", { name: "Introduction" });
    await expect(introBtn).toBeVisible();
    await expect(introBtn).toContainText("1");
    await expect(page.getByRole("button", { name: "Skills" })).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // Clicking a section opens Edit mode
  // -------------------------------------------------------------------------

  test("clicking a section in Browse opens Edit mode with textarea", async ({
    page,
  }) => {
    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: the section list moved onto the "edit" tab —
    // "review" opens by default.
    await page.getByTestId("sidebar-tab-edit").click();

    // Click Introduction section button
    await clickSectionButton(page, "Introduction");

    // SectionEditor textarea should appear
    const textarea = page.locator('[data-testid="section-textarea"]');
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    const value = await textarea.inputValue();
    expect(value.length).toBeGreaterThan(0);
  });

  // -------------------------------------------------------------------------
  // Edit -> Save -> preview iframe refreshes
  // -------------------------------------------------------------------------

  test("editing + saving updates the preview iframe", async ({ page }) => {
    let cvHtmlCallCount = 0;
    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      cvHtmlCallCount++;
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: cvHtmlCallCount > 1 ? MOCK_CV_HTML_UPDATED : MOCK_CV_HTML,
      });
    });

    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="cv-iframe"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: the section list moved onto the "edit" tab —
    // "review" opens by default.
    await page.getByTestId("sidebar-tab-edit").click();

    // Open Introduction section
    await clickSectionButton(page, "Introduction");

    // Edit textarea
    const textarea = page.locator('[data-testid="section-textarea"]');
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill("My edited introduction text for E2E test");

    // Save
    const saveBtn = page.locator('[data-testid="section-save"]');
    await expect(saveBtn).toBeEnabled({ timeout: 3_000 });
    await saveBtn.click();

    // SaveScopePrompt should appear — choose "Just this CV"
    await expect(page.locator('[data-testid="save-cv-only-btn"]')).toBeVisible({
      timeout: 3_000,
    });
    await page.click('[data-testid="save-cv-only-btn"]');

    // CV iframe stays visible (refresh triggered by onSectionSave -> onHtmlRefresh)
    await expect(page.locator('[data-testid="cv-iframe"]')).toBeVisible({
      timeout: 5_000,
    });
  });

  // -------------------------------------------------------------------------
  // Unsaved-changes guard when clicking "Back to overview"
  // ContentTab uses window.confirm() — Playwright catches it via dialog event
  // -------------------------------------------------------------------------

  test("unsaved edits trigger window.confirm when clicking back to overview", async ({
    page,
  }) => {
    let confirmFired = false;
    page.on("dialog", async (dialog) => {
      confirmFired = dialog.type() === "confirm";
      await dialog.accept();
    });

    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: the section list moved onto the "edit" tab —
    // "review" opens by default.
    await page.getByTestId("sidebar-tab-edit").click();

    // Open Introduction and make unsaved edit
    await clickSectionButton(page, "Introduction");
    const textarea = page.locator('[data-testid="section-textarea"]');
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill("Unsaved edit to trigger guard");

    // Click "Back to overview"
    await page.click('[data-testid="back-to-browse"]');

    // window.confirm() should have been triggered
    expect(confirmFired).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Gap card in Browse navigates to owning section
  // -------------------------------------------------------------------------

  test("clicking a section row in the Edit tab opens Edit mode for that section", async ({
    page,
  }) => {
    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: gap cards are gone from this page — ContentTab is
    // mounted with variant="sections" here, so gap findings render as rows in
    // the review surface instead (data-testid prefix `review-item-g<N>-`).
    // The section list on the "edit" tab is the surviving path into Edit mode;
    // Introduction carries a gap in this fixture, same as the gap card this
    // test used to click directly.
    await page.getByTestId("sidebar-tab-edit").click();
    await clickSectionButton(page, "Introduction");

    // Should be in Edit mode — back button visible
    await expect(page.locator('[data-testid="back-to-browse"]')).toBeVisible({
      timeout: 5_000,
    });
    // Section label heading
    await expect(page.getByRole("heading", { name: "Introduction" })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// KaileChat rewrite flow
// ---------------------------------------------------------------------------

test.describe("CV Section Editor — KaileChat Rewrite", () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_FLOW_STATE),
      });
    });

    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: MOCK_CV_HTML,
      });
    });

    // GET /api/cv/{id}/sections — fetched by ContentTab to populate section list
    await page.route(`**/api/cv/${TEST_CV_ID}/sections`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_CV_SECTIONS),
        });
      } else {
        await route.continue();
      }
    });

    await page.route(
      `**/api/cv/${TEST_CV_ID}/sections/**`,
      async (route) => {
        if (route.request().method() === "PATCH") {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
              html: MOCK_CV_HTML_UPDATED,
              overrides_applied: ["introduction"],
            }),
          });
        } else {
          await route.continue();
        }
      }
    );
  });

  test("'Kaile hilft' -> free-text -> rewrite -> suggestion appears -> Apply", async ({
    page,
  }) => {
    await page.route(
      `**/api/cv/${TEST_CV_ID}/sections/introduction/rewrite`,
      async (route) => {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            suggestion: "Erfahrener Python-Entwickler mit fÃ¼nf Jahren Erfahrung.",
          }),
        });
      }
    );

    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: the section list moved onto the "edit" tab —
    // "review" opens by default.
    await page.getByTestId("sidebar-tab-edit").click();

    // Open Introduction section
    await clickSectionButton(page, "Introduction");
    await expect(page.locator('[data-testid="section-textarea"]')).toBeVisible({
      timeout: 5_000,
    });

    // KaileChat should be visible below the editor
    const rewriteBtn = page.locator('[data-testid="kaile-rewrite-btn"]');
    await expect(rewriteBtn).toBeVisible({ timeout: 5_000 });

    // Type a direction and submit
    const directions = page.locator('[data-testid="kaile-directions-input"]');
    await directions.fill("I also have extensive Python experience");
    await rewriteBtn.click();

    // Suggestion should appear
    const suggestion = page.locator('[data-testid="kaile-suggestion"]');
    await expect(suggestion).toBeVisible({ timeout: 5_000 });
    expect(await suggestion.textContent()).toContain("Python");

    // Apply button
    const applyBtn = page.locator('[data-testid="apply-suggestion-btn"]');
    await expect(applyBtn).toBeVisible();
    await applyBtn.click();
  });

  test("gap chips are rendered and toggleable in KaileChat", async ({
    page,
  }) => {
    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // E058/ADR-081 cl. 2: the section list moved onto the "edit" tab —
    // "review" opens by default.
    await page.getByTestId("sidebar-tab-edit").click();

    // Open Introduction (which has a gap)
    await clickSectionButton(page, "Introduction");
    await expect(page.locator('[data-testid="section-textarea"]')).toBeVisible({
      timeout: 5_000,
    });

    // Gap chip should be visible and not selected by default
    const chip = page.locator('[data-testid="gap-chip-Python"]');
    await expect(chip).toBeVisible({ timeout: 5_000 });
    expect(await chip.getAttribute("data-selected")).toBe("false");

    // Click to toggle
    await chip.click();
    expect(await chip.getAttribute("data-selected")).toBe("true");
  });
});

// ---------------------------------------------------------------------------
// Actions tab
// ---------------------------------------------------------------------------

test.describe("CV Section Editor — Actions Tab", () => {
  test.beforeEach(async ({ page }) => {
    await page.route(`**/api/flow/${TEST_FLOW_ID}/state`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_FLOW_STATE),
      });
    });

    await page.route(`**/api/cv/${TEST_CV_ID}/html`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: MOCK_CV_HTML,
      });
    });

    // GET /api/cv/{id}/sections — fetched by ContentTab to populate section list
    await page.route(`**/api/cv/${TEST_CV_ID}/sections`, async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(MOCK_CV_SECTIONS),
        });
      } else {
        await route.continue();
      }
    });
  });

  test("Actions tab shows match score and download button", async ({ page }) => {
    await page.goto(CV_PAGE_URL);
    await expect(page.locator('[data-testid="refinement-sidebar"]')).toBeVisible({
      timeout: 10_000,
    });

    // Match score should be visible
    await expect(page.getByText("85%")).toBeVisible({ timeout: 3_000 });

    // Download PDF button is on the page action bar above the CV
    await expect(page.locator('[data-testid="document-download-btn"]')).toBeVisible();
  });
});
