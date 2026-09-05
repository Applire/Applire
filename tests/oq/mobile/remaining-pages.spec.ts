// tests/oq/mobile/remaining-pages.spec.ts

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

import { test, expect, type Page } from "@playwright/test";

/**
 * US230 — responsive sweep of the remaining pages (profile hub, My Documents,
 * application dossier) at 390x844, plus US229's deep-link prefill.
 *
 * ## Why this file does not use the body-overflow assertion
 *
 * The other three mobile specs assert `document.body.scrollWidth <=
 * window.innerWidth`. On these pages that assertion is **structurally unable to
 * fire**: every `(shell)` page wraps its content in `overflow-hidden` with an
 * inner `overflow-y-auto` scroller, so a row wider than the viewport is CLIPPED,
 * never pushed — the body stays exactly 390 px wide over a column the user
 * cannot reach. Measured on this branch before the fixes: 0 body overflow,
 * 9 over-wide elements, among them My Documents' Expires column and Open button
 * (324 px of box, 446 px of content) and the dossier's document label squeezed
 * to 31 px of a 66 px string, which rendered the language chip as "Engli".
 *
 * `assertNothingLost` below is the predicate that does fire: for every element
 * whose content is wider than its own box, walk up to the viewport and let the
 * first ancestor that does anything about overflow decide — `auto`/`scroll` is
 * the AC's "wide content scrolls in its own container", `hidden`/`clip` is a
 * loss. Deliberate `truncate` ellipsis is a design decision and is exempt, and
 * icon-font ligature elements are skipped because their text IS the glyph name
 * ("notifications") and measures as a word until the Material stylesheet lands.
 */

const JOB_ID = "job-mobile-0000-0000-0000-000000000001";
const APP_ID = "app-mobile-0000-0000-0000-000000000001";
const FLOW_ID = "flow-mobile-0000-0000-0000-000000000001";

const MOCK_APPLICATION = {
  id: APP_ID,
  job_analysis_id: JOB_ID,
  workflow_status: "completed",
  user_status: "tracking",
  company_name: "Rheinwerk Verpackungen GmbH",
  role_title: "Senior Software Engineer",
  notes: null,
  applied_at: null,
  deadline: null,
  source_url: null,
  submitted_cv_id: null,
  submitted_cover_letter_id: null,
  language_override: null,
  pinned_facts: [],
  flow_session_id: FLOW_ID,
  flow_current_step: "complete",
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:05:00Z",
  expires_at: "2028-09-01T10:05:00Z",
};

// Two ready versions with a language chip — the row shape that lost its label
// at 390 px: icon + label + chip on the left, "Open PDF" + "Mark as sent" on
// the right, the right group `shrink-0`.
const MOCK_CVS = [
  {
    cv_id: "cv-mobile-0000-0000-0000-000000000002",
    status: "ready",
    template: "classic_german",
    created_at: "2026-09-01T10:04:00Z",
    document_language: "en",
    origin: "pipeline",
  },
  {
    cv_id: "cv-mobile-0000-0000-0000-000000000001",
    status: "ready",
    template: "modern_swiss",
    created_at: "2026-08-30T09:00:00Z",
    document_language: "de",
    origin: "pipeline",
  },
];

const MOCK_DOCUMENTS = {
  items: [
    {
      cv_id: "cv-mobile-0000-0000-0000-000000000002",
      flow_id: FLOW_ID,
      role_title: "Senior Software Engineer",
      company_name: "Rheinwerk Verpackungen GmbH",
      template: "classic_german",
      status: "ready",
      created_at: "2026-09-01T10:04:00Z",
      // Deliberately inside the 7-day window: the Expires cell then carries the
      // amber warning, i.e. the widest content the clipped column ever holds.
      expires_at: new Date(Date.now() + 3 * 864e5).toISOString(),
      origin: "pipeline",
    },
    {
      cv_id: "cv-mobile-0000-0000-0000-000000000001",
      flow_id: FLOW_ID,
      role_title: "Platform Engineer",
      company_name: "Zweite AG",
      template: "modern_swiss",
      status: "ready",
      created_at: "2026-08-30T09:00:00Z",
      expires_at: new Date(Date.now() + 60 * 864e5).toISOString(),
      origin: "agent",
    },
  ],
  total: 2,
};

const MOCK_JOB_ANALYSIS = {
  id: JOB_ID,
  role_title: "Senior Software Engineer",
  company_name: "Rheinwerk Verpackungen GmbH",
  // The dossier's JD-summary block reads these three with a bare `.length`
  // (page.tsx:627/633/639). The real API cannot send them undefined — they are
  // non-optional on JobAnalysisResponse and coerced by a before-validator — but
  // an incomplete FIXTURE takes the whole page down through the error boundary,
  // which is how the first run of this spec failed.
  required_skills: ["Python", "FastAPI"],
  nice_to_have_skills: ["Kubernetes"],
  keywords: ["Backend", "DACH"],
  seniority_level: "senior",
  company_culture_signals: [],
  language_requirement: "de",
  jd_language: "de",
  scope_requirements: [],
  raw_text_hash: "hash-mobile-0001",
  source_url: null,
};

const MOCK_PROFILE = {
  profile: {
    personal_info: { name: "Emma Musterfrau", email: "emma@example.de" },
    work_experience: [
      { role: "Senior Software Engineer", employer: "TechVision GmbH", start_date: "2021-03" },
    ],
    skills: [{ name: "Python", level: "Expert" }],
  },
  completeness: 0.86,
  stats: { positions: 3, projects: 2, certifications: 1, data_points: 20 },
};

function json(body: unknown) {
  return { status: 200, contentType: "application/json", body: JSON.stringify(body) };
}

async function setupCommonMocks(page: Page) {
  await page.route("**/api/profile/import-jobs**", (r) => r.fulfill(json([])));
  await page.route("**/api/profile/exists", (r) =>
    r.fulfill(json({ exists: true, completeness_score: 0.86 })),
  );
  await page.route("**/api/profile/enrichment-history", (r) => r.fulfill(json({ items: [] })));
  await page.route("**/api/profile/health", (r) => r.fulfill(json({ score: 0.86, checks: [] })));
  await page.route("**/api/profile", (r) => r.fulfill(json(MOCK_PROFILE)));
  await page.route("**/api/applications", (r) => r.fulfill(json({ items: [MOCK_APPLICATION] })));
}

/**
 * The honest overflow predicate — see the file header for why the body-width
 * assertion cannot replace it. Returns the offenders so a failure names them.
 */
async function assertNothingLost(page: Page, label: string) {
  await page.evaluate(() => document.fonts.ready);
  const offenders = await page.evaluate(() => {
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      if (el.tagName === "IFRAME" || el.closest("nextjs-portal")) continue;
      if (el.classList.contains("material-symbols-outlined")) continue;
      const glyph = el.querySelector(".material-symbols-outlined");
      if (glyph && el.textContent === glyph.textContent) continue;
      if (el.clientWidth === 0 || el.scrollWidth - el.clientWidth <= 1) continue;
      if (el.classList.contains("truncate")) continue;

      let verdict = "LOST";
      for (let a: Element | null = el; a && a !== document.documentElement; a = a.parentElement) {
        const ox = getComputedStyle(a).overflowX;
        if (ox === "visible") continue;
        // `auto`/`scroll` is only a rescue if that ancestor ACTUALLY offers a
        // horizontal scroll. `overflow-y-auto` computes overflow-x to `auto`
        // too, so every (shell) page's <main> reads as a horizontal scroller
        // while scrolling nowhere — which is how the first version of this
        // predicate passed on both real defects (measured 2026-09-05).
        const offersScroll =
          (ox === "auto" || ox === "scroll") && a.scrollWidth - a.clientWidth > 1;
        verdict = offersScroll ? "scrolls" : "LOST";
        break;
      }
      if (verdict === "LOST") {
        const cls = typeof el.className === "string" ? el.className : "";
        out.push(
          `${el.tagName.toLowerCase()}.${cls.slice(0, 60)} ${el.clientWidth}->${el.scrollWidth} "${(
            el.textContent ?? ""
          )
            .replace(/\s+/g, " ")
            .trim()
            .slice(0, 40)}"`,
        );
      }
    }
    return out;
  });
  expect(offenders, `${label}: content clipped with no way to reach it`).toEqual([]);
}

async function shoot(page: Page, testInfo: import("@playwright/test").TestInfo, name: string) {
  const file = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  await testInfo.attach(name, { path: file, contentType: "image/png" });
}

test.describe("US230 — remaining pages at 390x844", () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page);
  });

  test("profile hub stacks single-column and loses nothing", async ({ page }, testInfo) => {
    await page.goto("/profile");
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible({ timeout: 10000 });
    await assertNothingLost(page, "profile hub");
    await shoot(page, testInfo, "profile-hub-mobile-390x844");
  });

  test("My Documents: the table scrolls inside its own container, it is not clipped", async ({
    page,
  }, testInfo) => {
    await page.route("**/api/documents**", (r) => r.fulfill(json(MOCK_DOCUMENTS)));
    await page.goto("/documents");
    const table = page.locator("table").first();
    await expect(table).toBeVisible({ timeout: 10000 });

    // The container must be over-wide AND scrollable. Before US230 it was
    // over-wide and `overflow-hidden`, which is what put the Expires column and
    // the row's own Open button outside the viewport unreachably.
    const wrapper = page.locator("div", { has: page.locator("> table") }).last();
    const geometry = await wrapper.evaluate((el) => ({
      clientWidth: el.clientWidth,
      scrollWidth: el.scrollWidth,
      overflowX: getComputedStyle(el).overflowX,
    }));
    expect(geometry.scrollWidth).toBeGreaterThan(geometry.clientWidth);
    expect(["auto", "scroll"]).toContain(geometry.overflowX);

    // And the last column is genuinely reachable by scrolling that container.
    const openButton = page.getByTestId("documents-table-open-btn").first();
    await openButton.scrollIntoViewIfNeeded();
    const box = await openButton.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);

    await assertNothingLost(page, "My Documents");
    await shoot(page, testInfo, "documents-mobile-390x844");
  });

  test("dossier: document rows stack so the label and its language chip survive", async ({
    page,
  }, testInfo) => {
    await page.route(`**/api/applications/${APP_ID}`, (r) => r.fulfill(json(MOCK_APPLICATION)));
    await page.route("**/api/cv?job_id=*", (r) => r.fulfill(json(MOCK_CVS)));
    await page.route("**/api/cover-letter/by-job/**", (r) => r.fulfill({ status: 404, body: "" }));
    await page.route(`**/api/job/${JOB_ID}`, (r) => r.fulfill(json(MOCK_JOB_ANALYSIS)));

    await page.goto(`/applications/${APP_ID}`);
    await expect(page.getByTestId("dossier-documents-zone")).toBeVisible({ timeout: 10000 });

    const row = page.getByTestId("dossier-doc-row").first();
    await expect(row).toBeVisible();

    // The defect this pins: the label column collapsed to 31 px because the
    // action group is `shrink-0` and the row was a single flex line. Below `sm`
    // the row stacks, so the label gets the row's full inner width.
    const label = row.locator("p").first();
    const labelBox = await label.boundingBox();
    const rowBox = await row.boundingBox();
    expect(labelBox).not.toBeNull();
    expect(rowBox).not.toBeNull();
    expect(labelBox!.width).toBeGreaterThan(rowBox!.width * 0.5);

    // The language chip is a non-truncating sibling of the label; when the row
    // was one line it was the thing that got cut ("Engli").
    const chip = page.getByTestId("dossier-doc-language").first();
    await expect(chip).toBeVisible();
    const chipBox = await chip.boundingBox();
    expect(chipBox!.x + chipBox!.width).toBeLessThanOrEqual(390);

    // Actions sit BELOW the label at this width, both fully inside the viewport.
    const pdf = page.getByTestId("dossier-doc-pdf").first();
    const pdfBox = await pdf.boundingBox();
    expect(pdfBox!.y).toBeGreaterThanOrEqual(labelBox!.y + labelBox!.height - 1);
    expect(pdfBox!.x + pdfBox!.width).toBeLessThanOrEqual(390);

    await assertNothingLost(page, "dossier");
    await shoot(page, testInfo, "dossier-mobile-390x844");
  });

  test("dossier: the tracking fields are touch-usable", async ({ page }) => {
    await page.route(`**/api/applications/${APP_ID}`, (r) => r.fulfill(json(MOCK_APPLICATION)));
    await page.route("**/api/cv?job_id=*", (r) => r.fulfill(json(MOCK_CVS)));
    await page.route("**/api/cover-letter/by-job/**", (r) => r.fulfill({ status: 404, body: "" }));
    await page.route(`**/api/job/${JOB_ID}`, (r) => r.fulfill(json(MOCK_JOB_ANALYSIS)));

    await page.goto(`/applications/${APP_ID}`);
    await expect(page.getByTestId("dossier-tracking-sidebar")).toBeVisible({ timeout: 10000 });

    // US230 AC: "Dossier edit fields (notes, deadline, status, mark-as-submitted)
    // usable on touch." 40 px is the smallest target the WCAG 2.2 AA minimum
    // (24x24 CSS px) and every mobile HIG agree is comfortable; assert height,
    // and that each field spans the column rather than being squeezed beside a
    // label.
    for (const id of ["dossier-tracking-deadline", "dossier-tracking-source", "dossier-tracking-notes"]) {
      const field = page.getByTestId(id);
      await expect(field, id).toBeVisible();
      const box = await field.boundingBox();
      expect(box, id).not.toBeNull();
      expect(box!.height, `${id} height`).toBeGreaterThanOrEqual(40);
      expect(box!.x + box!.width, `${id} right edge`).toBeLessThanOrEqual(390);
    }
  });
});

test.describe("US229 — share-target deep link at 390x844", () => {
  test.beforeEach(async ({ page }) => {
    await setupCommonMocks(page);
  });

  test("?jd_url= prefills Quick Tailor and does NOT start the analysis", async ({
    page,
  }, testInfo) => {
    // JF-E-Q.6's control is this NEGATIVE assertion. A prefill that also
    // submitted would satisfy every positive check on this page, so record the
    // POSTs and assert there were none.
    const posts: string[] = [];
    page.on("request", (r) => {
      if (r.method() === "POST") posts.push(r.url());
    });

    await page.goto("/dashboard?jd_url=https%3A%2F%2Fjobs.example.com%2Fsenior-engineer");
    const input = page.getByTestId("quick-tailor-url-input");
    await expect(input).toHaveValue("https://jobs.example.com/senior-engineer", { timeout: 10000 });
    await expect(page.getByTestId("quick-tailor-submit")).toBeEnabled();

    expect(posts.filter((u) => /\/api\/(job\/analyze|applications)/.test(u))).toEqual([]);
    await shoot(page, testInfo, "share-prefill-mobile-390x844");
  });

  test("?jd_text= prefills the text tab instead", async ({ page }) => {
    await page.goto("/dashboard?jd_text=Senior%20Engineer%20(m%2Fw%2Fd)%0A%0AWir%20suchen");
    await expect(page.getByTestId("quick-tailor-text-input")).toHaveValue(
      "Senior Engineer (m/w/d)\n\nWir suchen",
      { timeout: 10000 },
    );
    await expect(page.getByTestId("quick-tailor-url-input")).toHaveCount(0);
  });

  test("the manifest is served and declares the GET share target", async ({ page, request }) => {
    // The one part of US229 an OQ tier CAN prove end to end (the worker itself
    // is production-only, clause 4d, and is gated in lib/__tests__/sw.test.ts).
    await page.goto("/dashboard");
    await expect(page.locator('link[rel="manifest"]')).toHaveCount(1);

    const res = await request.get("/manifest.webmanifest");
    expect(res.status()).toBe(200);
    const manifest = await res.json();
    expect(manifest.share_target.method).toBe("GET");
    expect(manifest.share_target.action).toBe("/share-target");
    expect(manifest.display).toBe("standalone");

    for (const icon of manifest.icons) {
      const iconRes = await request.get(icon.src);
      expect(iconRes.status(), icon.src).toBe(200);
    }
    expect((await request.get("/sw.js")).status()).toBe(200);
    expect((await request.get("/offline.html")).status()).toBe(200);
  });

  test("the share-target handler normalises a link buried in `text`", async ({ page }) => {
    // The load-bearing case: Chrome and the LinkedIn app put the posting URL in
    // `text` with the title glued in front, and omit `url` entirely.
    await page.goto(
      "/share-target?title=Senior%20Engineer&text=Senior%20Engineer%20at%20TechVision%20https%3A%2F%2Fjobs.example.com%2F123",
    );
    await expect(page).toHaveURL(/\/dashboard\?jd_url=https%3A%2F%2Fjobs\.example\.com%2F123$/);
    await expect(page.getByTestId("quick-tailor-url-input")).toHaveValue(
      "https://jobs.example.com/123",
      { timeout: 10000 },
    );
  });
});
